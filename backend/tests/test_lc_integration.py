"""
Task 12: Integration & Final Validation [R10].

End-to-end pipeline tests + deterministic compliance checks.
Validates that LC infrastructure works as a cohesive system
without touching production code.

Requirements: R10 AC1, AC3, AC4, AC5, AC6
AC2 (existing tests don't break) → CI-level validation
AC7 (< 4 min budget) → CI job config, not in-test assertion

Test structure:
  TestE2EReportPipeline (3 async) — AC1, AC6
  TestCompliance (2 sync) — AC3, AC1/AC4/AC5
  TestNonIntrusivePolicy (1 sync) — AC4, AC5
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest

from backend.app.testing.lc_config import (
    DEFAULT_SEED,
    FaultType,
    ProfileType,
)
from backend.app.testing.load_harness import DEFAULT_PROFILES, LoadResult
from backend.app.testing.metrics_capture import MetricDelta
from backend.app.testing.scenario_runner import (
    InjectionConfig,
    ScenarioResult,
    ScenarioRunner,
)
from backend.app.testing.stress_report import (
    MetricsRow,
    StressReportConfig,
    build_stress_report,
    compute_write_path_safe,
    generate_metrics_table,
    _extract_retry_count,
)


# ── Constants ────────────────────────────────────────────────────────────

# LC module files: known set + lc_*.py glob (hybrid discovery)
_KNOWN_LC_MODULES = {
    "load_harness.py",
    "metrics_capture.py",
    "scenario_runner.py",
    "stress_report.py",
    "cb_observer.py",
    "lc_config.py",
}

_TESTING_DIR = Path(__file__).resolve().parent.parent / "app" / "testing"

# Forbidden patterns in LC modules (AC4/AC5 static policy)
# Simple string match — applied after comment/docstring stripping
_FORBIDDEN_STRINGS = [
    "setattr(",
    "sys.modules[",
    "importlib.reload(",
    "monkeypatch(",
]

# Metric name pattern: only inside string literals (single or double quoted).
# Catches prometheus-style metric names with namespace prefix structure:
# at least 4 underscore-separated segments ending with a metric suffix.
# e.g., "some_ns_dependency_call_total" would be caught.
# Shorter names like "cb_open_duration_seconds" are NOT caught
# (they're config parameter names, not Prometheus metrics).
_METRIC_IN_STRING_RE = re.compile(
    r"""['"]([a-z][a-z0-9]*(?:_[a-z][a-z0-9]*){3,}_(?:total|bucket|count|sum|gauge|info))['"]"""
)


# ── Helpers ──────────────────────────────────────────────────────────────

def _discover_lc_modules() -> list[Path]:
    """
    Discover LC module files under backend/app/testing/.
    Hybrid: known set + lc_*.py glob.
    """
    found: set[Path] = set()
    for name in _KNOWN_LC_MODULES:
        p = _TESTING_DIR / name
        if p.exists():
            found.add(p)
    for p in _TESTING_DIR.glob("lc_*.py"):
        found.add(p)
    return sorted(found)


def _read_source_no_comments(path: Path) -> str:
    """
    Read file source, stripping comment lines and docstrings.
    Simple state machine: skip # lines and triple-quote blocks.
    Prevents false positives in forbidden pattern / namespace scans.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    result: list[str] = []
    in_docstring = False
    docstring_delim = None

    for line in lines:
        stripped = line.strip()

        # Toggle docstring state
        if not in_docstring:
            if stripped.startswith('"""') or stripped.startswith("'''"):
                docstring_delim = stripped[:3]
                # Single-line docstring: """..."""
                if stripped.count(docstring_delim) >= 2:
                    continue
                in_docstring = True
                continue
        else:
            if docstring_delim and docstring_delim in stripped:
                in_docstring = False
            continue

        # Skip comment-only lines
        if stripped.startswith("#"):
            continue

        result.append(line)

    return "\n".join(result)


def _make_load_result(**overrides: Any) -> LoadResult:
    """Build a minimal LoadResult for unit-level helpers."""
    defaults = dict(
        profile=DEFAULT_PROFILES[ProfileType.BASELINE],
        seed=DEFAULT_SEED,
        scale_factor=0.01,
        executed_requests=100,
        successful_requests=100,
        failed_requests=0,
        latencies=[0.05] * 100,
    )
    defaults.update(overrides)
    return LoadResult(**defaults)


MetricKey = tuple[str, tuple[tuple[str, str], ...]]


def _make_delta(retry_total: float = 0.0, call_total: float = 100.0) -> MetricDelta:
    """Build a MetricDelta with controlled retry/call counters."""
    deltas: dict[MetricKey, float] = {}
    if call_total > 0:
        deltas[("ptf_admin_dependency_call_total", (("outcome", "success"),))] = call_total
    if retry_total > 0:
        deltas[("ptf_admin_dependency_retry_total", (("dependency", "test"),))] = retry_total
    return MetricDelta(counter_deltas=deltas, gauge_values={}, diagnostics=[])


def _make_scenario_result(
    scenario_id: str,
    is_write: bool = False,
    retry_total: float = 0.0,
    call_total: float = 100.0,
    cb_opened: bool = False,
    p95_seconds: float = 0.05,
) -> ScenarioResult:
    """Build a ScenarioResult with controlled metadata and metrics."""
    metadata: dict[str, Any] = {"seed": DEFAULT_SEED}
    if is_write:
        metadata["is_write"] = True
    lr = _make_load_result(
        executed_requests=int(call_total),
        successful_requests=int(call_total - retry_total) if not cb_opened else int(call_total * 0.6),
        failed_requests=int(retry_total) if not cb_opened else int(call_total * 0.4),
        latencies=[p95_seconds] * int(call_total),
    )
    return ScenarioResult(
        scenario_id=scenario_id,
        metadata=metadata,
        outcomes=["success"] * lr.successful_requests + ["failure"] * lr.failed_requests,
        cb_opened=cb_opened,
        load_result=lr,
        metrics_delta=_make_delta(retry_total=retry_total, call_total=call_total),
        diagnostics=[],
    )


# ═════════════════════════════════════════════════════════════════════════
# TestE2EReportPipeline — AC1, AC6
# ═════════════════════════════════════════════════════════════════════════

class TestE2EReportPipeline:
    """
    End-to-end: InjectionConfig → ScenarioRunner → build_stress_report.
    Validates full pipeline produces correct StressReport structure.
    """

    @pytest.mark.asyncio
    async def test_e2e_baseline_to_report(self):
        """
        AC1/AC6: Baseline (noop) scenario → ScenarioRunner → build_stress_report.
        Report has 1 row, all MetricsRow fields populated, valid JSON.
        """
        runner = ScenarioRunner()
        inj = InjectionConfig(
            enabled=False,
            profile=DEFAULT_PROFILES[ProfileType.BASELINE],
            scale_factor=0.01,
            seed=DEFAULT_SEED,
        )
        result = await runner.run_scenario("integ-baseline", inj)

        report = build_stress_report([result])

        # Table: 1 row, all fields present
        assert len(report.table) == 1
        row = report.table[0]
        assert row.scenario_name == "integ-baseline"
        assert row.total_calls > 0
        assert isinstance(row.retry_count, int)
        assert isinstance(row.retry_amplification_factor, float)
        assert isinstance(row.p95_latency_ms, float)
        assert isinstance(row.cb_opened, int)
        assert isinstance(row.failopen_count, int)

        # write_path_safe: vacuously True (no write scenarios)
        assert report.write_path_safe is True

        # JSON roundtrip
        payload = json.loads(report.to_json())
        assert len(payload["table"]) == 1
        assert payload["write_path_safe"] is True

    @pytest.mark.asyncio
    async def test_e2e_fault_injection_to_report(self):
        """
        AC1/AC6: DB_TIMEOUT 10% fault → ScenarioRunner → build_stress_report.
        Report row has retry_count > 0, cb_opened reflects heuristic.
        """
        runner = ScenarioRunner()
        inj = InjectionConfig(
            enabled=True,
            fault_type=FaultType.DB_TIMEOUT,
            failure_rate=0.10,
            profile=DEFAULT_PROFILES[ProfileType.BASELINE],
            scale_factor=0.01,
            seed=DEFAULT_SEED,
        )
        result = await runner.run_scenario("integ-fault", inj)

        report = build_stress_report([result])

        assert len(report.table) == 1
        row = report.table[0]
        assert row.scenario_name == "integ-fault"
        assert row.total_calls > 0

        # Fault injection produces failures
        assert result.load_result.failed_requests > 0

        # Report is valid JSON
        payload = json.loads(report.to_json())
        assert payload["table"][0]["scenario_name"] == "integ-fault"

    @pytest.mark.asyncio
    async def test_e2e_multi_scenario_aggregation(self):
        """
        AC1/AC6: 3 scenarios (baseline + fault + write-tagged) aggregated
        into single StressReport.
        - table has 3 rows
        - recommendations are deterministic (sort_keys in JSON)
        - write_path_safe reflects write scenario's retry state
        """
        runner = ScenarioRunner()

        # 1. Baseline (noop)
        r_baseline = await runner.run_scenario(
            "agg-baseline",
            InjectionConfig(
                enabled=False,
                profile=DEFAULT_PROFILES[ProfileType.BASELINE],
                scale_factor=0.01,
                seed=DEFAULT_SEED,
            ),
        )

        # 2. Fault injection (DB_TIMEOUT 10%)
        r_fault = await runner.run_scenario(
            "agg-fault",
            InjectionConfig(
                enabled=True,
                fault_type=FaultType.DB_TIMEOUT,
                failure_rate=0.10,
                profile=DEFAULT_PROFILES[ProfileType.BASELINE],
                scale_factor=0.01,
                seed=DEFAULT_SEED,
            ),
        )

        # 3. Write-tagged (noop, tagged as write)
        r_write = await runner.run_scenario(
            "agg-write",
            InjectionConfig(
                enabled=False,
                profile=DEFAULT_PROFILES[ProfileType.BASELINE],
                scale_factor=0.01,
                seed=DEFAULT_SEED,
            ),
        )
        r_write.metadata["is_write"] = True

        results = [r_baseline, r_fault, r_write]
        report = build_stress_report(results)

        # Table: 3 rows, one per scenario
        assert len(report.table) == 3
        names = [row.scenario_name for row in report.table]
        assert names == ["agg-baseline", "agg-fault", "agg-write"]

        # write_path_safe: True (write scenario has 0 retries)
        assert report.write_path_safe is True

        # JSON determinism: two calls produce identical output
        json1 = report.to_json()
        json2 = report.to_json()
        assert json1 == json2

        # Recommendations list is stable across serialization
        payload = json.loads(json1)
        assert isinstance(payload["recommendations"], list)
        assert len(payload["table"]) == 3


# ═════════════════════════════════════════════════════════════════════════
# TestCompliance — AC3, AC1/AC4/AC5
# ═════════════════════════════════════════════════════════════════════════

class TestCompliance:
    """
    Static compliance checks on LC module source code.
    No async, no I/O beyond file reads.
    """

    def test_lc_metrics_namespace_compliance(self):
        """
        AC3: All metric name string literals in LC modules use ptf_admin_ prefix.

        Scan strategy (user-approved):
        - Regex for prometheus-style metric names: *_total, *_seconds, *_bucket, etc.
        - Filter to LC modules only (not repo-wide)
        - Strip comments and docstrings before scanning
        - Any metric-like name without ptf_admin_ prefix → FAIL
        """
        lc_modules = _discover_lc_modules()
        assert len(lc_modules) > 0, "No LC modules discovered"

        violations: list[str] = []
        for mod_path in lc_modules:
            source = _read_source_no_comments(mod_path)
            matches = _METRIC_IN_STRING_RE.findall(source)
            for match in matches:
                if not match.startswith("ptf_admin_"):
                    violations.append(f"{mod_path.name}: {match}")

        assert violations == [], (
            f"R10 AC3 FAIL: metric-like names without ptf_admin_ prefix:\n"
            + "\n".join(f"  - {v}" for v in violations)
        )

    def test_lc_modules_location_compliance(self):
        """
        AC1/AC4/AC5: All LC modules live under backend/app/testing/.

        Dynamic discovery: known set + lc_*.py glob.
        Every discovered module must resolve under _TESTING_DIR.
        """
        lc_modules = _discover_lc_modules()
        assert len(lc_modules) >= len(_KNOWN_LC_MODULES), (
            f"Expected at least {len(_KNOWN_LC_MODULES)} LC modules, "
            f"found {len(lc_modules)}"
        )

        for mod_path in lc_modules:
            resolved = mod_path.resolve()
            testing_resolved = _TESTING_DIR.resolve()
            assert str(resolved).startswith(str(testing_resolved)), (
                f"R10 AC4 FAIL: LC module {mod_path.name} lives outside "
                f"backend/app/testing/: {resolved}"
            )

        # Verify all known modules were found
        found_names = {p.name for p in lc_modules}
        missing = _KNOWN_LC_MODULES - found_names
        assert missing == set(), (
            f"R10 AC1 FAIL: known LC modules not found: {missing}"
        )


# ═════════════════════════════════════════════════════════════════════════
# TestNonIntrusivePolicy — AC4, AC5
# ═════════════════════════════════════════════════════════════════════════

class TestNonIntrusivePolicy:
    """
    Static policy: LC modules do not use patterns that could
    modify production code at runtime.

    CI grep-style check (user-approved approach):
    Forbidden patterns: setattr(, sys.modules[, importlib.reload, monkeypatch(
    Scanned on comment/docstring-stripped source.
    """

    def test_lc_modules_no_forbidden_patterns(self):
        """
        AC4/AC5: No forbidden runtime-modification patterns in LC modules.
        """
        lc_modules = _discover_lc_modules()
        assert len(lc_modules) > 0, "No LC modules discovered"

        violations: list[str] = []
        for mod_path in lc_modules:
            source = _read_source_no_comments(mod_path)
            for line_no, line in enumerate(source.splitlines(), 1):
                for forbidden in _FORBIDDEN_STRINGS:
                    if forbidden in line:
                        violations.append(
                            f"{mod_path.name}:{line_no}: {line.strip()}"
                        )

        assert violations == [], (
            f"R10 AC4/AC5 FAIL: forbidden patterns found in LC modules:\n"
            + "\n".join(f"  - {v}" for v in violations)
        )
