"""
Task 4: GNK Checkpoint — LC temel altyapı doğrulaması.

Hard-locks GNK-1 (diagnostic payload contract), GNK-2 (determinism),
GNK-3 (minimum request enforcement) before Failure Matrix tests.

This file is the "foundation lock" — if any of these fail, the LC
infrastructure is broken and FM tests would be meaningless.
"""
from __future__ import annotations

import asyncio
import random

import pytest
from prometheus_client import CollectorRegistry

from backend.app.testing.lc_config import (
    DEFAULT_SEED,
    FaultType,
    MIN_REQUESTS_BY_PROFILE,
    ProfileType,
)
from backend.app.testing.load_harness import (
    DEFAULT_PROFILES,
    LoadHarness,
    LoadProfile,
    LoadResult,
)
from backend.app.testing.metrics_capture import (
    LC_WHITELIST,
    MetricDelta,
    MetricsCapture,
    _WHITELIST_NAMES,
)
from backend.app.testing.scenario_runner import (
    InjectionConfig,
    ScenarioResult,
    ScenarioRunner,
)
from backend.app.testing.stress_report import FailDiagnostic


# ── Helpers ──────────────────────────────────────────────────────────────

def _ci_profile() -> LoadProfile:
    return DEFAULT_PROFILES[ProfileType.BASELINE]


def _ci_injection(
    fault_type: FaultType = FaultType.DB_TIMEOUT,
    failure_rate: float = 0.5,
    seed: int = DEFAULT_SEED,
) -> InjectionConfig:
    return InjectionConfig(
        enabled=True,
        fault_type=fault_type,
        failure_rate=failure_rate,
        seed=seed,
        profile=_ci_profile(),
        scale_factor=0.01,
    )


def _noop_injection(seed: int = DEFAULT_SEED) -> InjectionConfig:
    return InjectionConfig(
        enabled=False,
        seed=seed,
        profile=_ci_profile(),
        scale_factor=0.01,
    )


# ═══════════════════════════════════════════════════════════════════════════
# GNK-1: Contract Completeness — Diagnostic Payload & Summary Schema
# ═══════════════════════════════════════════════════════════════════════════

class TestGNK1ContractCompleteness:
    """
    GNK-1: Every FAIL produces a FailDiagnostic with 6 mandatory fields.
    ScenarioResult.summary() contains all required keys.
    LoadResult.summary() contains all required keys.
    MetricDelta.summary() contains all required keys.
    """

    # ── FailDiagnostic schema ────────────────────────────────────────────

    def test_fail_diagnostic_has_six_fields(self):
        """FailDiagnostic dataclass has exactly the 6 GNK-1 fields."""
        diag = FailDiagnostic(
            scenario_id="test",
            dependency="dep",
            outcome="fail",
            observed=42,
            expected=100,
            seed=1337,
        )
        required = {"scenario_id", "dependency", "outcome", "observed", "expected", "seed"}
        assert required == set(diag.__dataclass_fields__.keys())

    def test_fail_diagnostic_values_accessible(self):
        diag = FailDiagnostic(
            scenario_id="s1", dependency="db", outcome="negative_counter_delta",
            observed=5.0, expected=">= 10.0", seed=42,
        )
        assert diag.scenario_id == "s1"
        assert diag.dependency == "db"
        assert diag.outcome == "negative_counter_delta"
        assert diag.observed == 5.0
        assert diag.expected == ">= 10.0"
        assert diag.seed == 42

    # ── ScenarioResult.summary() schema ──────────────────────────────────

    @pytest.mark.asyncio
    async def test_scenario_summary_required_keys_with_injection(self):
        """Injection scenario summary has all required top-level keys."""
        runner = ScenarioRunner()
        result = await runner.run_scenario("gnk1-inj", _ci_injection())
        s = result.summary()

        required_keys = {
            "scenario_id", "cb_opened", "diagnostic_count",
            "load", "metrics",
        }
        assert required_keys.issubset(set(s.keys())), (
            f"Missing keys: {required_keys - set(s.keys())}"
        )

    @pytest.mark.asyncio
    async def test_scenario_summary_required_keys_noop(self):
        """Noop scenario summary also has all required keys."""
        runner = ScenarioRunner()
        result = await runner.run_scenario("gnk1-noop", _noop_injection())
        s = result.summary()
        assert "scenario_id" in s
        assert "cb_opened" in s
        assert "diagnostic_count" in s
        assert "load" in s
        assert "metrics" in s

    # ── LoadResult.summary() schema ──────────────────────────────────────

    @pytest.mark.asyncio
    async def test_load_summary_required_keys(self):
        """LoadResult summary contains all GNK-1 required fields."""
        runner = ScenarioRunner()
        result = await runner.run_scenario("gnk1-load", _ci_injection())
        load_summary = result.load_result.summary()

        required = {
            "profile", "seed", "scale_factor",
            "planned_requests", "executed_requests",
            "successful_requests", "failed_requests",
            "circuit_open_count", "achieved_rps",
            "p50_seconds", "p95_seconds", "p99_seconds",
            "error_rate", "circuit_open_rate",
            "duration_ms", "invariant_ok",
        }
        assert required == set(load_summary.keys()), (
            f"Schema drift: {required.symmetric_difference(set(load_summary.keys()))}"
        )

    # ── MetricDelta.summary() schema ─────────────────────────────────────

    @pytest.mark.asyncio
    async def test_metrics_summary_required_keys(self):
        """MetricDelta summary contains all GNK-1 required fields."""
        runner = ScenarioRunner()
        result = await runner.run_scenario("gnk1-met", _ci_injection())
        met_summary = result.metrics_delta.summary()

        required = {
            "counter_deltas", "gauge_values",
            "retry_amplification", "invariant_ok", "diagnostic_count",
        }
        assert required == set(met_summary.keys()), (
            f"Schema drift: {required.symmetric_difference(set(met_summary.keys()))}"
        )

    # ── Diagnostics list always present ──────────────────────────────────

    @pytest.mark.asyncio
    async def test_diagnostics_always_list(self):
        """ScenarioResult.diagnostics is always a list (never None)."""
        for inj in [_ci_injection(), _noop_injection()]:
            runner = ScenarioRunner()
            result = await runner.run_scenario("gnk1-diag", inj)
            assert isinstance(result.diagnostics, list)

    # ── Whitelist metric count locked ────────────────────────────────────

    def test_whitelist_count_locked_at_five(self):
        """LC whitelist must have exactly 5 metrics — adding requires spec update."""
        assert len(LC_WHITELIST) == 5
        assert len(_WHITELIST_NAMES) == 5


# ═══════════════════════════════════════════════════════════════════════════
# GNK-2: Determinism — Same seed + same params → identical counts
# ═══════════════════════════════════════════════════════════════════════════

class TestGNK2Determinism:
    """
    GNK-2: Pure in-process callable with same seed → exact count equality.
    No time-based tolerance needed — target_fn is deterministic via random.Random(seed).
    """

    @pytest.mark.asyncio
    async def test_same_seed_identical_counts_baseline(self):
        """3 runs, same seed, BASELINE profile → identical success/fail counts."""
        inj = _ci_injection(failure_rate=0.3, seed=42)
        counts = []
        for _ in range(3):
            runner = ScenarioRunner()
            result = await runner.run_scenario("gnk2-base", inj)
            lr = result.load_result
            counts.append((lr.executed_requests, lr.successful_requests, lr.failed_requests))

        assert counts[0] == counts[1] == counts[2], (
            f"Determinism broken across 3 runs: {counts}"
        )

    @pytest.mark.asyncio
    async def test_same_seed_identical_counts_all_fault_types(self):
        """Each FaultType with same seed → identical counts across 2 runs."""
        for ft in FaultType:
            inj = _ci_injection(fault_type=ft, failure_rate=0.5, seed=9876)
            r1 = await ScenarioRunner().run_scenario("gnk2-ft-a", inj)
            r2 = await ScenarioRunner().run_scenario("gnk2-ft-b", inj)
            assert r1.load_result.successful_requests == r2.load_result.successful_requests, (
                f"Determinism broken for {ft.value}: "
                f"{r1.load_result.successful_requests} != {r2.load_result.successful_requests}"
            )
            assert r1.load_result.failed_requests == r2.load_result.failed_requests

    @pytest.mark.asyncio
    async def test_same_seed_same_cb_opened(self):
        """Same seed → same cb_opened decision."""
        for ft in [FaultType.DB_TIMEOUT, FaultType.EXTERNAL_5XX]:
            inj = _ci_injection(fault_type=ft, failure_rate=1.0, seed=DEFAULT_SEED)
            r1 = await ScenarioRunner().run_scenario("gnk2-cb-a", inj)
            r2 = await ScenarioRunner().run_scenario("gnk2-cb-b", inj)
            assert r1.cb_opened == r2.cb_opened, (
                f"CB decision non-deterministic for {ft.value}"
            )

    @pytest.mark.asyncio
    async def test_different_seeds_diverge(self):
        """Different seeds with 50% failure → different counts (extremely likely)."""
        inj_a = _ci_injection(failure_rate=0.5, seed=1)
        inj_b = _ci_injection(failure_rate=0.5, seed=99999)
        r_a = await ScenarioRunner().run_scenario("gnk2-div-a", inj_a)
        r_b = await ScenarioRunner().run_scenario("gnk2-div-b", inj_b)
        # With 200+ requests at 50%, different seeds almost certainly differ
        assert (
            r_a.load_result.successful_requests != r_b.load_result.successful_requests
            or r_a.load_result.failed_requests != r_b.load_result.failed_requests
        ), "Different seeds produced identical counts — extremely unlikely"

    @pytest.mark.asyncio
    async def test_invariant_check_deterministic(self):
        """LoadResult.invariant_check() always True for valid runs."""
        for seed in [1, 42, 1337, 99999]:
            inj = _ci_injection(failure_rate=0.5, seed=seed)
            result = await ScenarioRunner().run_scenario(f"gnk2-inv-{seed}", inj)
            assert result.load_result.invariant_check(), (
                f"Invariant broken for seed={seed}"
            )


# ═══════════════════════════════════════════════════════════════════════════
# GNK-3: Minimum Request Enforcement
# ═══════════════════════════════════════════════════════════════════════════

class TestGNK3MinRequestEnforcement:
    """
    GNK-3: Even with smallest valid scale_factor (0.01), minimum request
    counts are enforced per profile type.

    Baseline/Peak ≥ 200, Stress/Burst ≥ 500.
    """

    @pytest.mark.asyncio
    async def test_baseline_min_200_via_scenario(self):
        """Baseline profile through ScenarioRunner enforces ≥ 200 requests."""
        inj = InjectionConfig(
            enabled=False,
            profile=DEFAULT_PROFILES[ProfileType.BASELINE],
            scale_factor=0.01,
        )
        result = await ScenarioRunner().run_scenario("gnk3-base", inj)
        assert result.load_result.executed_requests >= 200, (
            f"Baseline only ran {result.load_result.executed_requests} requests"
        )

    @pytest.mark.asyncio
    async def test_peak_min_200_via_scenario(self):
        """Peak profile through ScenarioRunner enforces ≥ 200 requests."""
        inj = InjectionConfig(
            enabled=False,
            profile=DEFAULT_PROFILES[ProfileType.PEAK],
            scale_factor=0.01,
        )
        result = await ScenarioRunner().run_scenario("gnk3-peak", inj)
        assert result.load_result.executed_requests >= 200

    @pytest.mark.asyncio
    async def test_stress_min_500_via_scenario(self):
        """Stress profile through ScenarioRunner enforces ≥ 500 requests."""
        inj = InjectionConfig(
            enabled=False,
            profile=DEFAULT_PROFILES[ProfileType.STRESS],
            scale_factor=0.01,
        )
        result = await ScenarioRunner().run_scenario("gnk3-stress", inj)
        assert result.load_result.executed_requests >= 500

    @pytest.mark.asyncio
    async def test_burst_min_500_via_scenario(self):
        """Burst profile through ScenarioRunner enforces ≥ 500 requests."""
        inj = InjectionConfig(
            enabled=False,
            profile=DEFAULT_PROFILES[ProfileType.BURST],
            scale_factor=0.01,
        )
        result = await ScenarioRunner().run_scenario("gnk3-burst", inj)
        assert result.load_result.executed_requests >= 500

    def test_min_requests_config_locked(self):
        """MIN_REQUESTS_BY_PROFILE values are locked — changing requires spec update."""
        assert MIN_REQUESTS_BY_PROFILE[ProfileType.BASELINE] == 200
        assert MIN_REQUESTS_BY_PROFILE[ProfileType.PEAK] == 200
        assert MIN_REQUESTS_BY_PROFILE[ProfileType.STRESS] == 500
        assert MIN_REQUESTS_BY_PROFILE[ProfileType.BURST] == 500

    def test_scale_factor_below_001_raises(self):
        """scale_factor < 0.01 is rejected at LoadHarness level."""
        with pytest.raises(ValueError, match="scale_factor"):
            LoadHarness(seed=1, scale_factor=0.005)

    def test_all_profiles_have_min_requests(self):
        """Every ProfileType has a MIN_REQUESTS_BY_PROFILE entry."""
        for pt in ProfileType:
            assert pt in MIN_REQUESTS_BY_PROFILE, f"Missing min_requests for {pt.value}"
            assert MIN_REQUESTS_BY_PROFILE[pt] > 0


# ═══════════════════════════════════════════════════════════════════════════
# Cross-GNK: Integration sanity
# ═══════════════════════════════════════════════════════════════════════════

class TestCrossGNKSanity:
    """
    Cross-cutting checks that combine multiple GNK guarantees.
    """

    @pytest.mark.asyncio
    async def test_injection_scenario_full_contract(self):
        """
        Full injection scenario satisfies all three GNK rules simultaneously:
        - GNK-1: summary schema complete
        - GNK-2: deterministic (invariant_check passes)
        - GNK-3: min requests met
        """
        inj = _ci_injection(failure_rate=0.3, seed=42)
        result = await ScenarioRunner().run_scenario("cross-gnk", inj)

        # GNK-1: schema
        s = result.summary()
        assert "scenario_id" in s
        assert "load" in s
        assert "metrics" in s
        assert isinstance(result.diagnostics, list)

        # GNK-2: invariant
        assert result.load_result.invariant_check()

        # GNK-3: min requests
        assert result.load_result.executed_requests >= 200

    @pytest.mark.asyncio
    async def test_noop_scenario_full_contract(self):
        """Noop scenario also satisfies all GNK rules."""
        result = await ScenarioRunner().run_scenario("cross-noop", _noop_injection())

        assert "scenario_id" in result.summary()
        assert result.load_result.invariant_check()
        assert result.load_result.executed_requests >= 200
        assert result.load_result.failed_requests == 0
        assert result.metrics_delta.invariant_ok
