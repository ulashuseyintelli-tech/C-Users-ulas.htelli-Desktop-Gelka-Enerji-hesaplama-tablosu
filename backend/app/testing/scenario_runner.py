"""
ScenarioRunner — LC orkestrasyon bileşeni.

Feature: load-characterization, Task 3.1
Requirements: R3 (3.1–3.6), GNK-1, GNK-2

Orchestrates: FaultInjector → MetricsCapture snapshot → LoadHarness run → snapshot → delta → cleanup.
Pure async callable target_fn — no HTTP, no network.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from prometheus_client import CollectorRegistry

from .fault_injection import FaultInjector, InjectionPoint
from .lc_config import (
    DEFAULT_SEED,
    FM_EXPECTS_CB_OPEN,
    FaultType,
    LcRuntimeConfig,
    ProfileType,
)
from .load_harness import LoadHarness, LoadProfile, LoadResult, DEFAULT_PROFILES
from .metrics_capture import MetricDelta, MetricsCapture, MetricSnapshot
from .stress_report import FailDiagnostic

# ── FaultType → InjectionPoint mapping ───────────────────────────────────

_FAULT_TO_INJECTION: dict[FaultType, InjectionPoint] = {
    FaultType.DB_TIMEOUT: InjectionPoint.DB_TIMEOUT,
    FaultType.EXTERNAL_5XX: InjectionPoint.EXTERNAL_5XX_BURST,
    FaultType.KILLSWITCH: InjectionPoint.KILLSWITCH_TOGGLE,
    FaultType.RATE_LIMIT: InjectionPoint.RATE_LIMIT_SPIKE,
    FaultType.GUARD_ERROR: InjectionPoint.GUARD_INTERNAL_ERROR,
}


# ── Data classes ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class InjectionConfig:
    """
    Scenario injection configuration.

    enabled: whether fault injection is active
    fault_type: which fault to inject (None = noop)
    failure_rate: 0.0–1.0 probability of failure per request
    seed: deterministic RNG seed (GNK-2)
    profile: load profile to run (defaults to BASELINE)
    scale_factor: LoadHarness scale factor (default 0.1 for CI-safe)
    """
    enabled: bool = False
    fault_type: Optional[FaultType] = None
    failure_rate: float = 1.0
    seed: int = DEFAULT_SEED
    profile: LoadProfile = field(default_factory=lambda: DEFAULT_PROFILES[ProfileType.BASELINE])
    scale_factor: float = 0.1


@dataclass
class ScenarioResult:
    """
    Full scenario result with load data, metrics delta, and diagnostics.

    Backward compat: scenario_id, metadata, outcomes, cb_opened retained.
    New: load_result, metrics_delta, diagnostics.
    """
    scenario_id: str
    metadata: dict[str, Any] = field(default_factory=dict)
    outcomes: list[str] = field(default_factory=list)
    cb_opened: bool = False
    load_result: Optional[LoadResult] = None
    metrics_delta: Optional[MetricDelta] = None
    diagnostics: list[FailDiagnostic] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        """JSON-serializable summary for reporting."""
        s: dict[str, Any] = {
            "scenario_id": self.scenario_id,
            "cb_opened": self.cb_opened,
            "diagnostic_count": len(self.diagnostics),
        }
        if self.load_result is not None:
            s["load"] = self.load_result.summary()
        if self.metrics_delta is not None:
            s["metrics"] = self.metrics_delta.summary()
        s.update(self.metadata)
        return s


# ── ScenarioRunner ───────────────────────────────────────────────────────

class ScenarioRunner:
    """
    LC scenario orchestrator.

    Usage:
        from backend.app.ptf_metrics import PTFMetrics
        metrics = PTFMetrics(registry=CollectorRegistry())
        runner = ScenarioRunner(metrics=metrics)
        result = await runner.run_scenario("test-1", injection)

    Flow:
        1. _configure_injection(injection)
        2. take_snapshot (before)
        3. _create_target_fn(injection) → async callable
        4. LoadHarness.run_profile(profile, target_fn)
        5. take_snapshot (after)
        6. compute_delta(before, after)
        7. finally: FaultInjector.disable_all() + reset_instance()
    """

    def __init__(
        self,
        runtime: Optional[LcRuntimeConfig] = None,
        *,
        metrics_registry: Optional[CollectorRegistry] = None,
    ) -> None:
        self._runtime = runtime or LcRuntimeConfig()
        self._registry = metrics_registry or CollectorRegistry()
        self._capture = MetricsCapture(self._registry)

    @property
    def runtime(self) -> LcRuntimeConfig:
        return self._runtime

    @property
    def registry(self) -> CollectorRegistry:
        return self._registry

    async def run_scenario(
        self,
        scenario_id: str,
        injection: InjectionConfig,
    ) -> ScenarioResult:
        """
        Run a single scenario: inject → snapshot → load → snapshot → delta → cleanup.

        R3 AC4: finally block guarantees disable_all + reset_instance.
        """
        if not injection.enabled or injection.fault_type is None:
            return await self._run_noop(scenario_id, injection)

        injector = FaultInjector.get_instance()
        try:
            # 1. Configure injection
            self._configure_injection(injector, injection)

            # 2. Snapshot before
            before = self._capture.take_snapshot()

            # 3. Create target fn
            target_fn = self._create_target_fn(injection)

            # 4. Run load
            harness = LoadHarness(
                seed=injection.seed,
                scale_factor=injection.scale_factor,
            )
            load_result = await harness.run_profile(injection.profile, target_fn)

            # 5. Snapshot after
            after = self._capture.take_snapshot()

            # 6. Compute delta
            delta = self._capture.compute_delta(
                before, after,
                context_seed=injection.seed,
                context_scenario=scenario_id,
            )

            # 7. Build result
            diagnostics = list(delta.diagnostics)

            # CB heuristic (same as PR-2 logic for backward compat)
            expects_cb = FM_EXPECTS_CB_OPEN.get(injection.fault_type, False)
            actual_failure_rate = (
                load_result.error_rate if load_result.executed_requests > 0 else 0.0
            )
            cb_opened = expects_cb and actual_failure_rate >= self._runtime.cb_open_threshold

            return ScenarioResult(
                scenario_id=scenario_id,
                metadata={
                    "seed": injection.seed,
                    "fault_type": injection.fault_type.value,
                    "failure_rate": injection.failure_rate,
                    "scale_factor": injection.scale_factor,
                    "profile": injection.profile.profile_type.value,
                    "actual_failure_rate": actual_failure_rate,
                },
                outcomes=_build_outcomes(load_result),
                cb_opened=cb_opened,
                load_result=load_result,
                metrics_delta=delta,
                diagnostics=diagnostics,
            )
        finally:
            # R3 AC4: cleanup guarantee
            FaultInjector.get_instance().disable_all()
            FaultInjector.reset_instance()

    async def run_multi_instance_scenario(
        self,
        scenario_id: str,
        injection: InjectionConfig,
        instance_count: int = 2,
    ) -> list[ScenarioResult]:
        """
        Run N parallel instances with separate registries (LC-3).

        Each instance gets its own CollectorRegistry → no metric cross-talk.
        Returns list of ScenarioResult, one per instance.
        """
        results: list[ScenarioResult] = []
        for i in range(instance_count):
            instance_registry = CollectorRegistry()
            instance_runner = ScenarioRunner(
                runtime=self._runtime,
                metrics_registry=instance_registry,
            )
            instance_id = f"{scenario_id}-instance-{i}"
            result = await instance_runner.run_scenario(instance_id, injection)
            results.append(result)
        return results

    async def _run_noop(
        self,
        scenario_id: str,
        injection: InjectionConfig,
    ) -> ScenarioResult:
        """Noop scenario: no injection, just run load and capture metrics."""
        before = self._capture.take_snapshot()

        async def noop_fn() -> None:
            pass

        harness = LoadHarness(
            seed=injection.seed,
            scale_factor=injection.scale_factor,
        )
        load_result = await harness.run_profile(injection.profile, noop_fn)

        after = self._capture.take_snapshot()
        delta = self._capture.compute_delta(
            before, after,
            context_seed=injection.seed,
            context_scenario=scenario_id,
        )

        return ScenarioResult(
            scenario_id=scenario_id,
            metadata={
                "seed": injection.seed,
                "profile": injection.profile.profile_type.value,
                "scale_factor": injection.scale_factor,
            },
            outcomes=_build_outcomes(load_result),
            cb_opened=False,
            load_result=load_result,
            metrics_delta=delta,
            diagnostics=list(delta.diagnostics),
        )

    @staticmethod
    def _configure_injection(
        injector: FaultInjector,
        injection: InjectionConfig,
    ) -> None:
        """Map InjectionConfig → FaultInjector.enable()."""
        if injection.fault_type is None:
            return
        point = _FAULT_TO_INJECTION.get(injection.fault_type)
        if point is None:
            return
        injector.enable(
            point,
            params={
                "failure_rate": injection.failure_rate,
                "seed": injection.seed,
            },
        )

    @staticmethod
    def _create_target_fn(injection: InjectionConfig) -> Callable[..., Awaitable[Any]]:
        """
        Create a deterministic async callable that fails at injection.failure_rate.

        Uses random.Random(seed) for reproducibility (GNK-2).
        No real I/O — pure async simulation.
        """
        rng = random.Random(injection.seed)
        failure_rate = injection.failure_rate

        async def target_fn() -> None:
            if rng.random() < failure_rate:
                raise RuntimeError(
                    f"injected-{injection.fault_type.value if injection.fault_type else 'unknown'}"
                )

        return target_fn


# ── Module-level helpers ─────────────────────────────────────────────────

def _build_outcomes(load_result: LoadResult) -> list[str]:
    """Build outcome list from LoadResult counts (backward compat)."""
    outcomes: list[str] = []
    outcomes.extend(["success"] * load_result.successful_requests)
    outcomes.extend(["failure"] * load_result.failed_requests)
    return outcomes
