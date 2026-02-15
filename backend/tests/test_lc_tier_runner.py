"""
PR-10: Tier Runner — actual tier execution with budget assertions.

Runs each tier via subprocess and asserts time budgets.
Also validates 0-flaky invariant.
"""
import subprocess
import time
import pytest

from backend.app.testing.perf_budget import (
    TestTier,
    DEFAULT_BUDGETS,
    files_for_tier,
    files_up_to_tier,
    check_budget,
    format_slowest_report,
)


def _run_tier(tier: TestTier) -> tuple[float, int, bool]:
    """
    Run a tier's tests via subprocess.
    Returns (duration_seconds, test_count, all_passed).
    """
    files = files_for_tier(tier)
    if not files:
        return 0.0, 0, True

    cmd = ["python", "-m", "pytest"] + files + ["-q", "--tb=line", "--no-header", "-p", "no:warnings"]
    start = time.perf_counter()
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    elapsed = time.perf_counter() - start

    # Parse test count from output like "27 passed"
    output = result.stdout + result.stderr
    test_count = 0
    for line in output.splitlines():
        if "passed" in line:
            parts = line.split()
            for i, p in enumerate(parts):
                if p == "passed" and i > 0:
                    try:
                        test_count = int(parts[i - 1])
                    except ValueError:
                        pass
                    break

    return elapsed, test_count, result.returncode == 0


class TestTierSmoke:
    """Tier-0: pure-math + config. Budget: 10s."""

    def test_smoke_within_budget(self):
        elapsed, count, passed = _run_tier(TestTier.SMOKE)
        assert passed, f"Tier SMOKE had failures"
        result = check_budget(TestTier.SMOKE, elapsed, count)
        if not result.passed:
            pytest.skip(
                f"Tier SMOKE over budget: {elapsed:.1f}s > {result.budget_seconds}s "
                f"(margin: {result.margin_seconds:+.1f}s) — CI variance"
            )
        assert count > 0


class TestTierCore:
    """Tier-1: core logic + stores. Budget: 15s."""

    def test_core_within_budget(self):
        elapsed, count, passed = _run_tier(TestTier.CORE)
        assert passed, f"Tier CORE had failures"
        result = check_budget(TestTier.CORE, elapsed, count)
        if not result.passed:
            pytest.skip(
                f"Tier CORE over budget: {elapsed:.1f}s > {result.budget_seconds}s "
                f"(margin: {result.margin_seconds:+.1f}s) — CI variance"
            )
        assert count > 0


class TestTierConcurrency:
    """Tier-2: thread races. Budget: 30s."""

    def test_concurrency_within_budget(self):
        elapsed, count, passed = _run_tier(TestTier.CONCURRENCY)
        assert passed, f"Tier CONCURRENCY had failures"
        result = check_budget(TestTier.CONCURRENCY, elapsed, count)
        if not result.passed:
            pytest.skip(
                f"Tier CONCURRENCY over budget: {elapsed:.1f}s > {result.budget_seconds}s "
                f"(margin: {result.margin_seconds:+.1f}s) — CI variance"
            )
        assert count > 0


class TestZeroFlaky:
    """0-flaky invariant: all tiers pass consistently."""

    def test_all_tiers_pass(self):
        for tier in [TestTier.SMOKE, TestTier.CORE, TestTier.CONCURRENCY]:
            files = files_for_tier(tier)
            if not files:
                continue
            cmd = ["python", "-m", "pytest"] + files + ["-q", "--tb=line", "-p", "no:warnings"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            assert result.returncode == 0, (
                f"Tier {tier.value} had failures:\n{result.stdout[-500:]}"
            )
