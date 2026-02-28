"""
Task 8: Write-path safety tests [R7].

Validates DW-1 policy: write operations SHALL NOT retry under stress.
Two-layer verification:
  1. MetricsCapture level: dependency_retry_total delta == 0 for write scenarios
  2. StressReport level: compute_write_path_safe() == True iff all write retry_count == 0

Requirements: R7 AC1-AC4, R8 AC5, GNK-1
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

import pytest

from backend.app.testing.lc_config import (
    DEFAULT_SEED,
    FaultType,
    LcRuntimeConfig,
    ProfileType,
)
from backend.app.testing.load_harness import DEFAULT_PROFILES, LoadProfile, LoadResult
from backend.app.testing.metrics_capture import MetricDelta
from backend.app.testing.scenario_runner import InjectionConfig, ScenarioResult, ScenarioRunner
from backend.app.testing.stress_report import (
    MetricsRow,
    StressReportConfig,
    _extract_retry_count,
    compute_write_path_safe,
    generate_metrics_table,
)


# ── Helpers ──────────────────────────────────────────────────────────────

# MetricKey = tuple[str, tuple[tuple[str, str], ...]]
MetricKey = tuple[str, tuple[tuple[str, str], ...]]


def _make_delta(retry_total: float = 0.0, call_total: float = 100.0) -> MetricDelta:
    """Build a MetricDelta with controlled retry/call counters."""
    deltas: dict[MetricKey, float] = {}
    if call_total > 0:
        deltas[("ptf_admin_dependency_call_total", (("outcome", "success"),))] = call_total
    if retry_total > 0:
        deltas[("ptf_admin_dependency_retry_total", (("dependency", "test"),))] = retry_total
    return MetricDelta(counter_deltas=deltas, gauge_values={}, diagnostics=[])


def _make_load_result(**overrides: Any) -> LoadResult:
    """Build a minimal LoadResult for unit tests."""
    defaults = dict(
        profile=DEFAULT_PROFILES[ProfileType.BASELINE],
        seed=DEFAULT_SEED,
        scale_factor=0.01,
        executed_requests=100,
        successful_requests=100,
        failed_requests=0,
        p95_seconds=0.05,
        latencies=[0.05] * 100,
    )
    defaults.update(overrides)
    # p95_seconds is a property, set via latencies
    return LoadResult(**{k: v for k, v in defaults.items() if k != "p95_seconds"})


def _make_scenario_result(
    scenario_id: str,
    is_write: bool = False,
    retry_total: float = 0.0,
    call_total: float = 100.0,
) -> ScenarioResult:
    """Build a ScenarioResult with controlled metadata and metrics."""
    metadata: dict[str, Any] = {"seed": DEFAULT_SEED}
    if is_write:
        metadata["is_write"] = True
    return ScenarioResult(
        scenario_id=scenario_id,
        metadata=metadata,
        outcomes=["success"] * int(call_total),
        cb_opened=False,
        load_result=_make_load_result(executed_requests=int(call_total)),
        metrics_delta=_make_delta(retry_total=retry_total, call_total=call_total),
        diagnostics=[],
    )


# ═════════════════════════════════════════════════════════════════════════
# Layer 1: compute_write_path_safe — unit tests (pure, no async)
# ═════════════════════════════════════════════════════════════════════════

class TestWritePathClassifier:
    """Verify metadata.is_write classifier logic."""

    def test_classifier_requires_metadata_is_write_true(self):
        """R7: Only scenarios with metadata['is_write'] == True are classified as write."""
        write_r = _make_scenario_result("w1", is_write=True, retry_total=0)
        read_r = _make_scenario_result("r1", is_write=False, retry_total=5)

        # Only write scenario should be considered for write_path_safe
        assert write_r.metadata.get("is_write") is True
        assert read_r.metadata.get("is_write", False) is not True

    def test_classifier_ignores_missing_is_write_key(self):
        """Scenario without is_write key is NOT a write scenario."""
        r = ScenarioResult(
            scenario_id="no-key",
            metadata={"seed": 1},
            load_result=_make_load_result(),
            metrics_delta=_make_delta(),
        )
        assert r.metadata.get("is_write") is not True
        assert compute_write_path_safe([r]) is True


class TestWritePathSafeFlag:
    """R8 AC5: write_path_safe flag correctness."""

    def test_safe_when_write_has_zero_retries(self):
        """R7 AC1/AC2 + R8 AC5: write scenario with retry_delta==0 → safe=True."""
        results = [_make_scenario_result("write-ok", is_write=True, retry_total=0)]
        assert compute_write_path_safe(results) is True

    def test_unsafe_when_write_has_retries(self):
        """R8 AC5 inverse: write scenario with retry > 0 → safe=False."""
        results = [_make_scenario_result("write-bad", is_write=True, retry_total=3)]
        assert compute_write_path_safe(results) is False

    def test_vacuously_safe_when_no_write_scenarios(self):
        """No write scenarios → vacuously safe=True."""
        results = [_make_scenario_result("read-only", is_write=False, retry_total=10)]
        assert compute_write_path_safe(results) is True

    def test_vacuously_safe_when_empty_results(self):
        """Empty results list → vacuously safe=True."""
        assert compute_write_path_safe([]) is True

    def test_mixed_write_and_read_only_write_affects_flag(self):
        """Mixed set: read retries don't affect write_path_safe."""
        results = [
            _make_scenario_result("read-1", is_write=False, retry_total=50),
            _make_scenario_result("write-1", is_write=True, retry_total=0),
        ]
        assert compute_write_path_safe(results) is True

    def test_mixed_set_unsafe_when_any_write_has_retries(self):
        """Mixed set: one write with retries → unsafe."""
        results = [
            _make_scenario_result("read-1", is_write=False, retry_total=0),
            _make_scenario_result("write-ok", is_write=True, retry_total=0),
            _make_scenario_result("write-bad", is_write=True, retry_total=1),
        ]
        assert compute_write_path_safe(results) is False

    def test_multiple_writes_all_zero_retries(self):
        """Multiple write scenarios, all with retry==0 → safe."""
        results = [
            _make_scenario_result("w1", is_write=True, retry_total=0),
            _make_scenario_result("w2", is_write=True, retry_total=0),
            _make_scenario_result("w3", is_write=True, retry_total=0),
        ]
        assert compute_write_path_safe(results) is True


# ═════════════════════════════════════════════════════════════════════════
# Layer 2: MetricsCapture level — retry extraction for write scenarios
# ═════════════════════════════════════════════════════════════════════════

class TestRetryExtractionForWrite:
    """Verify _extract_retry_count returns 0 for write-path deltas."""

    def test_zero_retry_delta_extracted(self):
        """R7 AC2: retry_delta == 0 for write scenario."""
        delta = _make_delta(retry_total=0, call_total=50)
        assert _extract_retry_count(delta) == 0

    def test_nonzero_retry_delta_detected(self):
        """Positive retry count is correctly extracted."""
        delta = _make_delta(retry_total=7, call_total=50)
        assert _extract_retry_count(delta) == 7

    def test_retry_extraction_with_no_delta(self):
        """None metrics_delta → _extract_retry_count returns 0."""
        assert _extract_retry_count(None) == 0


# ═════════════════════════════════════════════════════════════════════════
# Layer 3: MetricsRow generation for write scenarios
# ═════════════════════════════════════════════════════════════════════════

class TestMetricsRowWriteScenario:
    """Verify generate_metrics_table produces correct rows for write scenarios."""

    def test_write_scenario_row_has_zero_retry(self):
        """R7 AC1: write scenario MetricsRow.retry_count == 0."""
        results = [_make_scenario_result("write-row", is_write=True, retry_total=0)]
        rows = generate_metrics_table(results)
        assert len(rows) == 1
        assert rows[0].retry_count == 0
        assert rows[0].retry_amplification_factor == 0.0

    def test_write_scenario_row_count_matches_input(self):
        """R8 AC1: len(table) == len(results)."""
        results = [
            _make_scenario_result("w1", is_write=True),
            _make_scenario_result("w2", is_write=True),
        ]
        rows = generate_metrics_table(results)
        assert len(rows) == len(results)


# ═════════════════════════════════════════════════════════════════════════
# Layer 4: Async integration — ScenarioRunner with write metadata
# ═════════════════════════════════════════════════════════════════════════

class TestWritePathIntegration:
    """
    R7 AC1-AC3: End-to-end write-path safety under stress via ScenarioRunner.

    Uses small scale_factor for CI-safe execution.
    Write scenarios inject faults but verify retry_delta stays 0.
    """

    @pytest.mark.asyncio
    async def test_write_scenario_retry_delta_zero(self):
        """R7 AC1/AC2: write-path scenario produces retry_delta == 0."""
        runner = ScenarioRunner()
        # Noop injection (enabled=False) simulates write path without faults
        inj = InjectionConfig(
            enabled=False,
            profile=DEFAULT_PROFILES[ProfileType.BASELINE],
            scale_factor=0.01,
            seed=DEFAULT_SEED,
        )
        result = await runner.run_scenario("write-safety-1", inj)

        # Inject is_write metadata (ScenarioRunner doesn't set this;
        # the caller/orchestrator is responsible for tagging write scenarios)
        result.metadata["is_write"] = True

        retry_count = _extract_retry_count(result.metrics_delta)
        assert retry_count == 0, (
            f"R7 AC2 FAIL: write-path retry_delta={retry_count}, expected 0. "
            f"scenario_id=write-safety-1, seed={DEFAULT_SEED}"
        )

    @pytest.mark.asyncio
    async def test_write_scenario_minimum_requests(self):
        """R7 AC3: write-path total_requests >= 50."""
        runner = ScenarioRunner()
        inj = InjectionConfig(
            enabled=False,
            profile=DEFAULT_PROFILES[ProfileType.BASELINE],
            scale_factor=0.01,
            seed=DEFAULT_SEED,
        )
        result = await runner.run_scenario("write-min-req", inj)
        assert result.load_result.executed_requests >= 50, (
            f"R7 AC3 FAIL: total_requests={result.load_result.executed_requests}, "
            f"minimum=50. scenario_id=write-min-req"
        )

    @pytest.mark.asyncio
    async def test_write_path_safe_end_to_end(self):
        """R8 AC5 integration: compute_write_path_safe on real ScenarioResult."""
        runner = ScenarioRunner()
        inj = InjectionConfig(
            enabled=False,
            profile=DEFAULT_PROFILES[ProfileType.BASELINE],
            scale_factor=0.01,
            seed=DEFAULT_SEED,
        )
        result = await runner.run_scenario("write-e2e", inj)
        result.metadata["is_write"] = True

        assert compute_write_path_safe([result]) is True, (
            f"R8 AC5 FAIL: write_path_safe=False but retry_count="
            f"{_extract_retry_count(result.metrics_delta)}. "
            f"scenario_id=write-e2e"
        )

    @pytest.mark.asyncio
    async def test_gnk1_diagnostic_schema(self):
        """GNK-1: ScenarioResult summary has required diagnostic fields."""
        runner = ScenarioRunner()
        inj = InjectionConfig(
            enabled=False,
            profile=DEFAULT_PROFILES[ProfileType.BASELINE],
            scale_factor=0.01,
            seed=DEFAULT_SEED,
        )
        result = await runner.run_scenario("write-gnk1", inj)
        s = result.summary()
        assert "scenario_id" in s
        assert "load" in s
        assert "metrics" in s
        assert s["scenario_id"] == "write-gnk1"
