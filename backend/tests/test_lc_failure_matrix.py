"""
PR-2: Failure Matrix tests (FM-1..FM-5) + determinism invariant (5.1) + CB OPEN guarantee (5.2).
"""
import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from backend.app.testing.lc_config import (
    FaultType,
    FM_EXPECTS_CB_OPEN,
    DEFAULT_SEED,
)
from backend.app.testing.scenario_runner import (
    ScenarioRunner,
    InjectionConfig,
    ScenarioResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_fm(fault_type: FaultType, failure_rate: float = 1.0, seed: int = DEFAULT_SEED) -> ScenarioResult:
    runner = ScenarioRunner()
    injection = InjectionConfig(
        enabled=True,
        fault_type=fault_type,
        failure_rate=failure_rate,
        seed=seed,
    )
    return runner.run_scenario(
        scenario_id=f"fm-{fault_type.value}",
        injection=injection,
        request_count=200,
    )


# ---------------------------------------------------------------------------
# FM-1: DB Timeout
# ---------------------------------------------------------------------------

class TestFM1DbTimeout:
    def test_100pct_failure_all_outcomes_failure(self):
        res = _run_fm(FaultType.DB_TIMEOUT, failure_rate=1.0)
        assert all(o == "failure" for o in res.outcomes)
        assert res.metadata["actual_failure_rate"] == 1.0

    def test_0pct_failure_all_outcomes_success(self):
        res = _run_fm(FaultType.DB_TIMEOUT, failure_rate=0.0)
        assert all(o == "success" for o in res.outcomes)
        assert res.metadata["actual_failure_rate"] == 0.0


# ---------------------------------------------------------------------------
# FM-2: External 5xx Burst
# ---------------------------------------------------------------------------

class TestFM2External5xx:
    def test_100pct_failure(self):
        res = _run_fm(FaultType.EXTERNAL_5XX, failure_rate=1.0)
        assert res.metadata["actual_failure_rate"] == 1.0
        assert res.cb_opened is True

    def test_partial_failure(self):
        res = _run_fm(FaultType.EXTERNAL_5XX, failure_rate=0.3, seed=42)
        rate = res.metadata["actual_failure_rate"]
        assert 0.1 <= rate <= 0.6  # stochastic but bounded


# ---------------------------------------------------------------------------
# FM-3: Killswitch Toggle
# ---------------------------------------------------------------------------

class TestFM3Killswitch:
    def test_100pct_failure_no_cb(self):
        res = _run_fm(FaultType.KILLSWITCH, failure_rate=1.0)
        assert res.metadata["actual_failure_rate"] == 1.0
        # Killswitch bypasses CB
        assert res.cb_opened is False


# ---------------------------------------------------------------------------
# FM-4: Rate Limit Spike
# ---------------------------------------------------------------------------

class TestFM4RateLimit:
    def test_100pct_failure_no_cb(self):
        res = _run_fm(FaultType.RATE_LIMIT, failure_rate=1.0)
        assert res.metadata["actual_failure_rate"] == 1.0
        # Rate limit is pre-CB
        assert res.cb_opened is False


# ---------------------------------------------------------------------------
# FM-5: Guard Internal Error
# ---------------------------------------------------------------------------

class TestFM5GuardError:
    def test_100pct_failure_cb_opens(self):
        res = _run_fm(FaultType.GUARD_ERROR, failure_rate=1.0)
        assert res.metadata["actual_failure_rate"] == 1.0
        assert res.cb_opened is True


# ---------------------------------------------------------------------------
# 5.1: Determinism invariant — same seed → same outcomes
# ---------------------------------------------------------------------------

class TestDeterminismInvariant:
    def test_same_seed_same_outcomes(self):
        """Two runs with identical seed must produce identical outcome lists."""
        for ft in FaultType:
            r1 = _run_fm(ft, failure_rate=0.5, seed=9999)
            r2 = _run_fm(ft, failure_rate=0.5, seed=9999)
            assert r1.outcomes == r2.outcomes, f"Determinism broken for {ft}"
            assert r1.metadata == r2.metadata

    @given(
        seed=st.integers(min_value=0, max_value=2**31 - 1),
        fault_type=st.sampled_from(list(FaultType)),
    )
    @settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow])
    def test_determinism_pbt(self, seed: int, fault_type: FaultType):
        """PBT: for any seed+fault_type, two runs are identical."""
        r1 = _run_fm(fault_type, failure_rate=0.5, seed=seed)
        r2 = _run_fm(fault_type, failure_rate=0.5, seed=seed)
        assert r1.outcomes == r2.outcomes
        assert r1.cb_opened == r2.cb_opened


# ---------------------------------------------------------------------------
# 5.2: CB OPEN guarantee — 100% failure on CB-triggering faults → cb_opened
# ---------------------------------------------------------------------------

class TestCbOpenGuarantee:
    @pytest.mark.parametrize("fault_type", [
        ft for ft, expects in FM_EXPECTS_CB_OPEN.items() if expects
    ])
    def test_100pct_failure_opens_cb(self, fault_type: FaultType):
        """100% failure rate on CB-triggering fault types must set cb_opened=True."""
        res = _run_fm(fault_type, failure_rate=1.0)
        assert res.cb_opened is True, (
            f"{fault_type.value}: expected cb_opened=True at 100% failure"
        )

    @pytest.mark.parametrize("fault_type", [
        ft for ft, expects in FM_EXPECTS_CB_OPEN.items() if not expects
    ])
    def test_100pct_failure_no_cb(self, fault_type: FaultType):
        """100% failure rate on non-CB fault types must NOT set cb_opened."""
        res = _run_fm(fault_type, failure_rate=1.0)
        assert res.cb_opened is False, (
            f"{fault_type.value}: expected cb_opened=False (non-CB fault)"
        )
