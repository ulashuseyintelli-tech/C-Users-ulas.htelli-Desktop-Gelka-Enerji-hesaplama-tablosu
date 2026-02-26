"""
Load Harness — async yük üreteci.

Deterministic seed-based load generation with isolated metrics.
Supports 4 profile types: baseline, peak, stress, burst.

Feature: load-characterization, Task 1.1
Requirements: R1 (1.1–1.7), GNK-1, GNK-3
"""
from __future__ import annotations

import asyncio
import json
import math
import random
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable, Final, Optional

from .lc_config import ProfileType, MIN_REQUESTS_BY_PROFILE, RPS_TOL_PCT, DEFAULT_SEED


@dataclass(frozen=True)
class LoadProfile:
    profile_type: ProfileType
    target_rps: float
    duration_seconds: float

    @property
    def min_requests(self) -> int:
        return MIN_REQUESTS_BY_PROFILE[self.profile_type]

    @property
    def target_requests(self) -> int:
        return max(self.min_requests, int(math.ceil(self.target_rps * self.duration_seconds)))


@dataclass(frozen=True)
class CallOutcome:
    """Single call result."""
    success: bool
    duration_seconds: float
    error: Optional[str] = None
    circuit_open: bool = False


@dataclass
class LoadResult:
    """Aggregated load run result with summary statistics."""
    profile: LoadProfile
    seed: int
    scale_factor: float
    started_at_ms: int = 0
    finished_at_ms: int = 0
    planned_requests: int = 0
    executed_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    circuit_open_count: int = 0
    achieved_rps: float = 0.0
    latencies: list[float] = field(default_factory=list)

    @property
    def p50_seconds(self) -> float:
        return self._percentile(0.50)

    @property
    def p95_seconds(self) -> float:
        return self._percentile(0.95)

    @property
    def p99_seconds(self) -> float:
        return self._percentile(0.99)

    @property
    def error_rate(self) -> float:
        if self.executed_requests == 0:
            return 0.0
        return self.failed_requests / self.executed_requests

    @property
    def circuit_open_rate(self) -> float:
        if self.executed_requests == 0:
            return 0.0
        return self.circuit_open_count / self.executed_requests

    def _percentile(self, pct: float) -> float:
        if not self.latencies:
            return 0.0
        s = sorted(self.latencies)
        idx = int(math.ceil(pct * len(s))) - 1
        return s[max(0, idx)]

    def invariant_check(self) -> bool:
        """R1 AC5: total == success + failed."""
        return self.executed_requests == self.successful_requests + self.failed_requests

    def summary(self) -> dict[str, Any]:
        """JSON-serializable summary."""
        return {
            "profile": self.profile.profile_type.value,
            "seed": self.seed,
            "scale_factor": round(self.scale_factor, 4),
            "planned_requests": self.planned_requests,
            "executed_requests": self.executed_requests,
            "successful_requests": self.successful_requests,
            "failed_requests": self.failed_requests,
            "circuit_open_count": self.circuit_open_count,
            "achieved_rps": round(self.achieved_rps, 2),
            "p50_seconds": round(self.p50_seconds, 6),
            "p95_seconds": round(self.p95_seconds, 6),
            "p99_seconds": round(self.p99_seconds, 6),
            "error_rate": round(self.error_rate, 4),
            "circuit_open_rate": round(self.circuit_open_rate, 4),
            "duration_ms": self.finished_at_ms - self.started_at_ms,
            "invariant_ok": self.invariant_check(),
        }

    def summary_json(self) -> str:
        return json.dumps(self.summary(), sort_keys=True, separators=(",", ":"))


DEFAULT_PROFILES: Final[dict[ProfileType, LoadProfile]] = {
    ProfileType.BASELINE: LoadProfile(ProfileType.BASELINE, target_rps=50.0, duration_seconds=10.0),
    ProfileType.PEAK: LoadProfile(ProfileType.PEAK, target_rps=200.0, duration_seconds=10.0),
    ProfileType.STRESS: LoadProfile(ProfileType.STRESS, target_rps=500.0, duration_seconds=5.0),
    ProfileType.BURST: LoadProfile(ProfileType.BURST, target_rps=1000.0, duration_seconds=0.5),
}


class LoadHarness:
    """
    Async load generator with deterministic seed-based scheduling.

    Usage:
        harness = LoadHarness(seed=1337, scale_factor=0.1)
        result = await harness.run_profile(profile, target_fn)

    target_fn: async callable that performs one "request".
               Should raise CircuitOpenError if CB is open.
               Should raise on failure.
               Returns on success.
    """

    def __init__(
        self,
        seed: int = DEFAULT_SEED,
        scale_factor: float = 1.0,
        concurrency: int = 5,
        now_ms_fn: Optional[Callable[[], int]] = None,
    ) -> None:
        if scale_factor < 0.01:
            raise ValueError(f"scale_factor {scale_factor} < 0.01 (R1 AC4)")
        self._seed = seed
        self._scale_factor = scale_factor
        self._concurrency = concurrency
        self._rng = random.Random(seed)
        self._now_ms_fn = now_ms_fn

    @property
    def seed(self) -> int:
        return self._seed

    @property
    def scale_factor(self) -> float:
        return self._scale_factor

    async def run_profile(
        self,
        profile: LoadProfile,
        target_fn: Callable[..., Awaitable[Any]],
    ) -> LoadResult:
        """
        Run a load profile against target_fn.

        For BURST profiles: multiple short windows in a loop.
        For others: single window.
        """
        scaled_rps = profile.target_rps * self._scale_factor
        scaled_duration = profile.duration_seconds * self._scale_factor
        planned = max(
            profile.min_requests,
            int(math.ceil(scaled_rps * scaled_duration)),
        )

        # Enforce GNK-3 minimum
        planned = max(planned, profile.min_requests)

        result = LoadResult(
            profile=profile,
            seed=self._seed,
            scale_factor=self._scale_factor,
            planned_requests=planned,
        )

        if profile.profile_type == ProfileType.BURST:
            # Burst: run in short windows, repeat until planned count reached
            await self._run_burst(result, target_fn, planned, scaled_duration)
        else:
            # Single window
            await self._run_window(result, target_fn, planned, scaled_duration)

        return result

    async def _run_window(
        self,
        result: LoadResult,
        target_fn: Callable[..., Awaitable[Any]],
        request_count: int,
        duration_seconds: float,
    ) -> None:
        """Run a single load window with concurrency-limited requests."""
        sem = asyncio.Semaphore(self._concurrency)
        result.started_at_ms = int(time.time() * 1000)

        # Calculate inter-request delay for target RPS
        if duration_seconds > 0 and request_count > 0:
            delay = duration_seconds / request_count
        else:
            delay = 0.0

        tasks: list[asyncio.Task] = []
        for i in range(request_count):
            task = asyncio.create_task(self._timed_call(sem, target_fn, result))
            tasks.append(task)
            if delay > 0 and i < request_count - 1:
                await asyncio.sleep(delay)

        # Wait for all in-flight tasks
        await asyncio.gather(*tasks, return_exceptions=True)

        result.finished_at_ms = int(time.time() * 1000)
        elapsed_s = max(0.001, (result.finished_at_ms - result.started_at_ms) / 1000.0)
        result.achieved_rps = result.executed_requests / elapsed_s

    async def _run_burst(
        self,
        result: LoadResult,
        target_fn: Callable[..., Awaitable[Any]],
        total_requests: int,
        window_duration: float,
    ) -> None:
        """Burst: fire all requests as fast as possible in concurrent batches."""
        sem = asyncio.Semaphore(self._concurrency)
        result.started_at_ms = int(time.time() * 1000)

        tasks: list[asyncio.Task] = []
        for _ in range(total_requests):
            task = asyncio.create_task(self._timed_call(sem, target_fn, result))
            tasks.append(task)

        await asyncio.gather(*tasks, return_exceptions=True)

        result.finished_at_ms = int(time.time() * 1000)
        elapsed_s = max(0.001, (result.finished_at_ms - result.started_at_ms) / 1000.0)
        result.achieved_rps = result.executed_requests / elapsed_s

    async def _timed_call(
        self,
        sem: asyncio.Semaphore,
        target_fn: Callable[..., Awaitable[Any]],
        result: LoadResult,
    ) -> None:
        """Execute a single timed call with semaphore-based concurrency control."""
        async with sem:
            start = time.perf_counter()
            try:
                await target_fn()
                duration = time.perf_counter() - start
                result.executed_requests += 1
                result.successful_requests += 1
                result.latencies.append(duration)
            except Exception as exc:
                duration = time.perf_counter() - start
                result.executed_requests += 1
                result.failed_requests += 1
                result.latencies.append(duration)
                # Detect CircuitOpenError by class name (avoid import cycle)
                if type(exc).__name__ == "CircuitOpenError":
                    result.circuit_open_count += 1

    @staticmethod
    def within_rps_tolerance(target_rps: float, achieved_rps: float) -> bool:
        """R1 AC3: ±30% tolerance check."""
        if target_rps <= 0:
            return True
        tol = RPS_TOL_PCT * target_rps
        return (target_rps - tol) <= achieved_rps <= (target_rps + tol)

    # ── Backward-compatible API (used by chaos tests) ────────────────────

    def plan(self, profile: LoadProfile) -> int:
        """Return planned request count for a profile."""
        planned = profile.target_requests
        if planned < profile.min_requests:
            raise ValueError("planned_requests < min_requests (GNK-3 violated)")
        return planned

    def run_dry(self, profile: LoadProfile, *, executed_requests: Optional[int] = None) -> "DryRunResult":
        """
        Dry-run that returns a result without doing real async load.
        Used by chaos/clock tests for timestamp determinism.
        """
        now_fn = self._now_ms_fn or (lambda: int(time.time() * 1000))
        start = int(now_fn())
        planned = self.plan(profile)
        exec_count = planned if executed_requests is None else executed_requests
        if exec_count < profile.min_requests:
            raise ValueError("executed_requests < min_requests (GNK-3 violated)")

        achieved = float(exec_count) / float(profile.duration_seconds) if profile.duration_seconds > 0 else float("inf")
        sf = achieved / profile.target_rps if profile.target_rps > 0 else float("inf")
        if sf < 0.01:
            raise ValueError("scale_factor < 0.01")

        end = int(now_fn())
        return DryRunResult(
            profile=profile,
            started_at_ms=start,
            finished_at_ms=end,
            planned_requests=planned,
            executed_requests=exec_count,
            achieved_rps=achieved,
            scale_factor=sf,
        )


@dataclass(frozen=True)
class DryRunResult:
    """Backward-compatible result for run_dry() — used by chaos tests."""
    profile: LoadProfile
    started_at_ms: int
    finished_at_ms: int
    planned_requests: int
    executed_requests: int
    achieved_rps: float
    scale_factor: float
