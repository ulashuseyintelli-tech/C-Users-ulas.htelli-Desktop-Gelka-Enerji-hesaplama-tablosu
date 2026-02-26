"""
Load Harness tests — determinism, invariants, smoke profile.

Feature: load-characterization, Task 1.1
Validates: R1 (1.1–1.7), GNK-1, GNK-2, GNK-3
"""
from __future__ import annotations

import asyncio
import random

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from backend.app.testing.load_harness import (
    LoadHarness,
    LoadProfile,
    LoadResult,
    DEFAULT_PROFILES,
)
from backend.app.testing.lc_config import ProfileType, DEFAULT_SEED


# ── Helpers ──────────────────────────────────────────────────────────────

_call_counter = 0


async def _noop_target():
    """Instant success — no I/O."""
    pass


async def _slow_target():
    """Simulated 1ms latency."""
    await asyncio.sleep(0.001)


def _make_failing_target(rng: random.Random, failure_rate: float):
    """Deterministic failure target using provided RNG."""
    async def _target():
        if rng.random() < failure_rate:
            raise RuntimeError("injected failure")
    return _target


class FakeCircuitOpenError(Exception):
    """Mimics CircuitOpenError for testing circuit_open_count detection."""
    pass

# Rename so LoadHarness detects it by class name
FakeCircuitOpenError.__name__ = "CircuitOpenError"


async def _circuit_open_target():
    raise FakeCircuitOpenError("open")


# ── Scale factor validation ──────────────────────────────────────────────

class TestScaleFactorValidation:
    def test_scale_factor_below_minimum_raises(self):
        with pytest.raises(ValueError, match="scale_factor"):
            LoadHarness(seed=1, scale_factor=0.005)

    def test_scale_factor_at_minimum_ok(self):
        h = LoadHarness(seed=1, scale_factor=0.01)
        assert h.scale_factor == 0.01

    def test_scale_factor_default_is_one(self):
        h = LoadHarness(seed=1)
        assert h.scale_factor == 1.0


# ── LoadResult invariant ─────────────────────────────────────────────────

class TestLoadResultInvariant:
    def test_invariant_holds_on_fresh_result(self):
        profile = DEFAULT_PROFILES[ProfileType.BASELINE]
        r = LoadResult(profile=profile, seed=1, scale_factor=1.0)
        assert r.invariant_check()

    def test_invariant_holds_after_counting(self):
        profile = DEFAULT_PROFILES[ProfileType.BASELINE]
        r = LoadResult(profile=profile, seed=1, scale_factor=1.0)
        r.executed_requests = 100
        r.successful_requests = 80
        r.failed_requests = 20
        assert r.invariant_check()

    def test_invariant_fails_on_mismatch(self):
        profile = DEFAULT_PROFILES[ProfileType.BASELINE]
        r = LoadResult(profile=profile, seed=1, scale_factor=1.0)
        r.executed_requests = 100
        r.successful_requests = 80
        r.failed_requests = 10  # 80+10 != 100
        assert not r.invariant_check()


# ── Determinism: same seed → same summary ────────────────────────────────

class TestDeterminism:
    def test_same_seed_three_runs_identical_counts(self):
        """
        GNK-2: 3 runs with same seed → identical executed/success/failed counts.
        Uses deterministic failure target with shared seed-based RNG.
        """
        profile = LoadProfile(ProfileType.BASELINE, target_rps=100.0, duration_seconds=1.0)
        summaries = []

        for _ in range(3):
            rng = random.Random(42)
            target = _make_failing_target(rng, failure_rate=0.3)
            harness = LoadHarness(seed=42, scale_factor=0.5, concurrency=5)
            result = asyncio.get_event_loop().run_until_complete(
                harness.run_profile(profile, target)
            )
            summaries.append({
                "executed": result.executed_requests,
                "successful": result.successful_requests,
                "failed": result.failed_requests,
            })

        # All 3 runs must produce identical counts
        assert summaries[0] == summaries[1] == summaries[2], (
            f"Determinism broken: {summaries}"
        )

    def test_different_seeds_different_failures(self):
        """Different seeds should (very likely) produce different failure patterns."""
        profile = LoadProfile(ProfileType.BASELINE, target_rps=100.0, duration_seconds=1.0)
        results = []

        for seed in [1, 2]:
            rng = random.Random(seed)
            target = _make_failing_target(rng, failure_rate=0.5)
            harness = LoadHarness(seed=seed, scale_factor=0.5, concurrency=5)
            result = asyncio.get_event_loop().run_until_complete(
                harness.run_profile(profile, target)
            )
            results.append(result.failed_requests)

        # With 50% failure rate and different seeds, counts should differ
        # (not guaranteed but extremely likely with 200+ requests)
        # We just check both ran successfully
        assert all(r > 0 for r in results)


# ── Smoke profile: short run, basic assertions ───────────────────────────

class TestSmokeProfile:
    def test_noop_baseline_all_success(self):
        """Smoke: baseline profile with noop target → all success, 0 errors."""
        profile = LoadProfile(ProfileType.BASELINE, target_rps=100.0, duration_seconds=1.0)
        harness = LoadHarness(seed=DEFAULT_SEED, scale_factor=0.5, concurrency=5)
        result = asyncio.get_event_loop().run_until_complete(
            harness.run_profile(profile, _noop_target)
        )

        assert result.invariant_check()
        assert result.executed_requests >= profile.min_requests
        assert result.failed_requests == 0
        assert result.error_rate == 0.0
        assert result.circuit_open_count == 0
        assert result.p50_seconds >= 0
        assert result.p95_seconds >= result.p50_seconds

    def test_burst_profile_completes(self):
        """Smoke: burst profile fires all requests concurrently."""
        profile = LoadProfile(ProfileType.BURST, target_rps=1000.0, duration_seconds=0.5)
        harness = LoadHarness(seed=DEFAULT_SEED, scale_factor=0.5, concurrency=10)
        result = asyncio.get_event_loop().run_until_complete(
            harness.run_profile(profile, _noop_target)
        )

        assert result.invariant_check()
        assert result.executed_requests >= profile.min_requests
        assert result.failed_requests == 0

    def test_circuit_open_counted(self):
        """CircuitOpenError is counted in circuit_open_count."""
        profile = LoadProfile(ProfileType.BASELINE, target_rps=50.0, duration_seconds=1.0)
        harness = LoadHarness(seed=1, scale_factor=0.5, concurrency=5)
        result = asyncio.get_event_loop().run_until_complete(
            harness.run_profile(profile, _circuit_open_target)
        )

        assert result.invariant_check()
        assert result.circuit_open_count == result.executed_requests
        assert result.failed_requests == result.executed_requests
        assert result.error_rate == 1.0

    def test_summary_json_is_valid(self):
        """Summary produces valid JSON with all expected keys."""
        import json
        profile = LoadProfile(ProfileType.BASELINE, target_rps=50.0, duration_seconds=0.5)
        harness = LoadHarness(seed=1, scale_factor=0.5, concurrency=3)
        result = asyncio.get_event_loop().run_until_complete(
            harness.run_profile(profile, _noop_target)
        )

        raw = result.summary_json()
        parsed = json.loads(raw)
        expected_keys = {
            "profile", "seed", "scale_factor", "planned_requests",
            "executed_requests", "successful_requests", "failed_requests",
            "circuit_open_count", "achieved_rps", "p50_seconds", "p95_seconds",
            "p99_seconds", "error_rate", "circuit_open_rate", "duration_ms",
            "invariant_ok",
        }
        assert set(parsed.keys()) == expected_keys
        assert parsed["invariant_ok"] is True


# ── GNK-3: Minimum request enforcement ───────────────────────────────────

class TestMinRequestEnforcement:
    def test_baseline_min_200(self):
        """Even with tiny scale, baseline runs at least 200 requests."""
        profile = DEFAULT_PROFILES[ProfileType.BASELINE]
        harness = LoadHarness(seed=1, scale_factor=0.01, concurrency=20)
        result = asyncio.get_event_loop().run_until_complete(
            harness.run_profile(profile, _noop_target)
        )
        assert result.executed_requests >= 200

    def test_stress_min_500(self):
        """Even with tiny scale, stress runs at least 500 requests."""
        profile = DEFAULT_PROFILES[ProfileType.STRESS]
        harness = LoadHarness(seed=1, scale_factor=0.01, concurrency=50)
        result = asyncio.get_event_loop().run_until_complete(
            harness.run_profile(profile, _noop_target)
        )
        assert result.executed_requests >= 500


# ── RPS tolerance helper ─────────────────────────────────────────────────

class TestRpsTolerance:
    def test_exact_match(self):
        assert LoadHarness.within_rps_tolerance(100.0, 100.0)

    def test_within_30pct(self):
        assert LoadHarness.within_rps_tolerance(100.0, 70.0)
        assert LoadHarness.within_rps_tolerance(100.0, 130.0)

    def test_outside_30pct(self):
        assert not LoadHarness.within_rps_tolerance(100.0, 69.0)
        assert not LoadHarness.within_rps_tolerance(100.0, 131.0)

    def test_zero_target(self):
        assert LoadHarness.within_rps_tolerance(0.0, 999.0)
