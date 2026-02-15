from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Optional

from .lc_config import LcRuntimeConfig, FaultType, FM_EXPECTS_CB_OPEN, DEFAULT_SEED
from .fault_injection import FaultInjector, InjectionPoint

# Map FaultType → InjectionPoint for wiring
_FAULT_TO_INJECTION: dict[FaultType, InjectionPoint] = {
    FaultType.DB_TIMEOUT: InjectionPoint.DB_TIMEOUT,
    FaultType.EXTERNAL_5XX: InjectionPoint.EXTERNAL_5XX_BURST,
    FaultType.KILLSWITCH: InjectionPoint.KILLSWITCH_TOGGLE,
    FaultType.RATE_LIMIT: InjectionPoint.RATE_LIMIT_SPIKE,
    FaultType.GUARD_ERROR: InjectionPoint.GUARD_INTERNAL_ERROR,
}


@dataclass(frozen=True)
class InjectionConfig:
    """
    PR-2: expanded with fault type, failure rate, and seed determinism.
    """
    enabled: bool = False
    fault_type: Optional[FaultType] = None
    failure_rate: float = 1.0  # 0.0–1.0
    seed: int = DEFAULT_SEED


@dataclass(frozen=True)
class ScenarioResult:
    scenario_id: str
    metadata: dict[str, Any]
    outcomes: list[str] = field(default_factory=list)
    cb_opened: bool = False


class ScenarioRunner:
    """
    PR-2: injection-aware scenario runner.
    Runs deterministic simulated scenarios for failure matrix validation.
    """

    def __init__(self, runtime: Optional[LcRuntimeConfig] = None):
        self._runtime = runtime or LcRuntimeConfig()

    @property
    def runtime(self) -> LcRuntimeConfig:
        return self._runtime

    def run_noop(self) -> ScenarioResult:
        return ScenarioResult(
            scenario_id="noop",
            metadata={
                "seed": self._runtime.seed,
                "eval_interval_seconds": self._runtime.eval_interval_seconds,
            },
        )

    def run_scenario(
        self,
        scenario_id: str,
        injection: InjectionConfig,
        request_count: int = 200,
    ) -> ScenarioResult:
        """
        PR-2: deterministic simulated scenario with fault injection.
        Uses seed-based RNG to decide per-request outcomes.
        Does NOT do real I/O — pure simulation for FM smoke + determinism.
        """
        if not injection.enabled or injection.fault_type is None:
            return self.run_noop()

        rng = random.Random(injection.seed)
        outcomes: list[str] = []
        failure_count = 0

        for _ in range(request_count):
            if rng.random() < injection.failure_rate:
                outcomes.append("failure")
                failure_count += 1
            else:
                outcomes.append("success")

        # CB heuristic: if failure_rate >= threshold, CB should open
        # for fault types that trigger CB (local heuristic, PR-3/4 wires real CB)
        expects_cb = FM_EXPECTS_CB_OPEN.get(injection.fault_type, False)
        actual_failure_rate = failure_count / request_count if request_count > 0 else 0.0
        cb_opened = expects_cb and actual_failure_rate >= 0.5

        return ScenarioResult(
            scenario_id=scenario_id,
            metadata={
                "seed": injection.seed,
                "fault_type": injection.fault_type.value,
                "failure_rate": injection.failure_rate,
                "request_count": request_count,
                "actual_failure_rate": actual_failure_rate,
                "failure_count": failure_count,
            },
            outcomes=outcomes,
            cb_opened=cb_opened,
        )
