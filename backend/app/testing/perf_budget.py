"""
PR-10: Performance Budgeting + Test Tiering + Flake Sentinel.

Provides:
- Tier definitions with time budgets
- Per-run timing capture and budget assertion
- Slowest-tests report
- Flake sentinel (rolling window flake detection)

Pure data structures — no pytest plugin magic. Tests call these explicitly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Tier definitions
# ---------------------------------------------------------------------------

class TestTier(str, Enum):
    SMOKE = "smoke"           # Tier-0: pure-math, config, basic wiring
    CORE = "core"             # Tier-1: stores, SLO, canary, policy, ops
    CONCURRENCY = "concurrency"  # Tier-2: races, multi-instance, threads
    SOAK = "soak"             # Tier-3: large PBT, nightly


@dataclass(frozen=True)
class TierBudget:
    tier: TestTier
    max_seconds: float
    description: str = ""


DEFAULT_BUDGETS: dict[TestTier, TierBudget] = {
    TestTier.SMOKE: TierBudget(TestTier.SMOKE, 10.0, "Pure-math + config < 10s"),
    TestTier.CORE: TierBudget(TestTier.CORE, 15.0, "Core logic + stores < 15s"),
    TestTier.CONCURRENCY: TierBudget(TestTier.CONCURRENCY, 30.0, "Thread races < 30s"),
    TestTier.SOAK: TierBudget(TestTier.SOAK, 120.0, "Large PBT / nightly < 120s"),
}


# ---------------------------------------------------------------------------
# Tier → test file mapping
# ---------------------------------------------------------------------------

TIER_FILE_MAP: dict[TestTier, list[str]] = {
    TestTier.SMOKE: [
        "test_lc_config.py",
        "test_lc_report.py",
        "test_lc_load_harness.py",
        "test_lc_failure_matrix.py",
        "test_lc_invariants.py",
        "test_lc_ops_contract.py",
    ],
    TestTier.CORE: [
        "test_lc_multi_instance.py",
        "test_lc_alert_validation.py",
        "test_lc_chaos_time.py",
        "test_lc_chaos_io.py",
        "test_lc_chaos_payload.py",
        "test_lc_chaos_splitbrain.py",
        "test_lc_slo_evaluator.py",
        "test_lc_canary_gate.py",
        "test_lc_pipeline_gate.py",
        "test_lc_policy_engine.py",
        "test_lc_override_governance.py",
        "test_lc_orchestrator.py",
        "test_lc_policy_canary.py",
        "test_lc_drift_monitor.py",
        "test_lc_state_store.py",
    ],
    TestTier.CONCURRENCY: [
        "test_lc_multi_instance_races.py",
        "test_lc_concurrency_semantics.py",
    ],
    TestTier.SOAK: [],  # future: large PBT runs
}


def files_for_tier(tier: TestTier, prefix: str = "backend/tests/") -> list[str]:
    """Return full paths for a tier's test files."""
    return [prefix + f for f in TIER_FILE_MAP.get(tier, [])]


def files_up_to_tier(tier: TestTier, prefix: str = "backend/tests/") -> list[str]:
    """Return files for this tier and all lower tiers."""
    order = [TestTier.SMOKE, TestTier.CORE, TestTier.CONCURRENCY, TestTier.SOAK]
    result = []
    for t in order:
        result.extend(files_for_tier(t, prefix))
        if t == tier:
            break
    return result


# ---------------------------------------------------------------------------
# Timing capture
# ---------------------------------------------------------------------------

@dataclass
class TestTiming:
    name: str
    duration_seconds: float
    tier: TestTier = TestTier.CORE


@dataclass
class TierRunResult:
    tier: TestTier
    total_seconds: float
    test_count: int
    budget_seconds: float
    passed: bool
    slowest: list[TestTiming] = field(default_factory=list)

    @property
    def margin_seconds(self) -> float:
        return self.budget_seconds - self.total_seconds


def check_budget(
    tier: TestTier,
    total_seconds: float,
    test_count: int,
    timings: list[TestTiming] | None = None,
    budgets: dict[TestTier, TierBudget] | None = None,
) -> TierRunResult:
    """Check if a tier run is within budget."""
    b = (budgets or DEFAULT_BUDGETS).get(tier)
    if b is None:
        return TierRunResult(
            tier=tier, total_seconds=total_seconds, test_count=test_count,
            budget_seconds=999.0, passed=True,
        )
    slowest = sorted(timings or [], key=lambda t: t.duration_seconds, reverse=True)[:10]
    return TierRunResult(
        tier=tier,
        total_seconds=total_seconds,
        test_count=test_count,
        budget_seconds=b.max_seconds,
        passed=total_seconds <= b.max_seconds,
        slowest=slowest,
    )


# ---------------------------------------------------------------------------
# Slowest tests report
# ---------------------------------------------------------------------------

def format_slowest_report(result: TierRunResult) -> str:
    """Format a human-readable slowest tests report."""
    lines = [
        f"Tier: {result.tier.value}",
        f"Total: {result.total_seconds:.2f}s / {result.budget_seconds:.2f}s "
        f"({'PASS' if result.passed else 'FAIL'})",
        f"Tests: {result.test_count}",
        f"Margin: {result.margin_seconds:+.2f}s",
    ]
    if result.slowest:
        lines.append("Slowest:")
        for t in result.slowest:
            lines.append(f"  {t.duration_seconds:.3f}s  {t.name}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Flake Sentinel
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RunRecord:
    run_id: int
    test_name: str
    passed: bool


class FlakeSentinel:
    """
    Detects flaky tests over a rolling window.
    A test is flaky if it has both passes and failures in the window.
    """

    def __init__(self, window_size: int = 20):
        self._window = window_size
        self._records: list[RunRecord] = []

    def record(self, run_id: int, test_name: str, passed: bool) -> None:
        self._records.append(RunRecord(run_id, test_name, passed))
        # Trim to window
        if len(self._records) > self._window * 100:
            self._records = self._records[-self._window * 100:]

    @property
    def records(self) -> list[RunRecord]:
        return list(self._records)

    def detect_flaky(self) -> list[str]:
        """Return test names that are flaky in the current window."""
        # Get last N unique run_ids
        run_ids = sorted(set(r.run_id for r in self._records))[-self._window:]
        recent = [r for r in self._records if r.run_id in run_ids]

        # Group by test name
        by_test: dict[str, set[bool]] = {}
        for r in recent:
            by_test.setdefault(r.test_name, set()).add(r.passed)

        return sorted(name for name, outcomes in by_test.items() if len(outcomes) > 1)

    def is_clean(self) -> bool:
        """True if no flaky tests detected."""
        return len(self.detect_flaky()) == 0
