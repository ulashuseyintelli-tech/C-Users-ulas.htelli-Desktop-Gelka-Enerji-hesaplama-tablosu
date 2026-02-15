from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final, Optional

from .lc_config import ProfileType, MIN_REQUESTS_BY_PROFILE, RPS_TOL_PCT


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
        # Deterministic rounding rule
        return max(self.min_requests, int(math.ceil(self.target_rps * self.duration_seconds)))


@dataclass(frozen=True)
class LoadResult:
    profile: LoadProfile
    started_at_ms: int
    finished_at_ms: int
    planned_requests: int
    executed_requests: int
    achieved_rps: float
    scale_factor: float  # achieved_rps / target_rps


DEFAULT_PROFILES: Final[dict[ProfileType, LoadProfile]] = {
    ProfileType.BASELINE: LoadProfile(ProfileType.BASELINE, target_rps=50.0, duration_seconds=10.0),
    ProfileType.PEAK: LoadProfile(ProfileType.PEAK, target_rps=200.0, duration_seconds=10.0),
    ProfileType.STRESS: LoadProfile(ProfileType.STRESS, target_rps=500.0, duration_seconds=5.0),
    ProfileType.BURST: LoadProfile(ProfileType.BURST, target_rps=1000.0, duration_seconds=0.5),
}


class LoadHarness:
    """
    PR-1: skeleton. Actual async execution comes later.
    For now, we validate profile + compute deterministic planned request counts.
    """

    def __init__(self, now_ms_fn):
        self._now_ms_fn = now_ms_fn

    def plan(self, profile: LoadProfile) -> int:
        planned = profile.target_requests
        if planned < profile.min_requests:
            # Should never happen due to target_requests logic, but keep guard.
            raise ValueError("planned_requests < min_requests (GNK-3 violated)")
        return planned

    def run_dry(self, profile: LoadProfile, *, executed_requests: Optional[int] = None) -> LoadResult:
        """
        PR-1: dry-run that returns a LoadResult without doing real async load.
        executed_requests defaults to planned_requests.
        """
        start = int(self._now_ms_fn())
        planned = self.plan(profile)
        exec_count = planned if executed_requests is None else executed_requests
        if exec_count < profile.min_requests:
            raise ValueError("executed_requests < min_requests (GNK-3 violated)")

        # Deterministic achieved_rps based on executed count and duration
        achieved = float(exec_count) / float(profile.duration_seconds) if profile.duration_seconds > 0 else float("inf")
        scale_factor = achieved / profile.target_rps if profile.target_rps > 0 else float("inf")

        # R1 AC4: scale_factor lower bound check
        if scale_factor < 0.01:
            raise ValueError("scale_factor < 0.01")

        end = int(self._now_ms_fn())

        return LoadResult(
            profile=profile,
            started_at_ms=start,
            finished_at_ms=end,
            planned_requests=planned,
            executed_requests=exec_count,
            achieved_rps=achieved,
            scale_factor=scale_factor,
        )

    @staticmethod
    def within_rps_tolerance(target_rps: float, achieved_rps: float) -> bool:
        # R1 AC3: ±30%
        if target_rps <= 0:
            return True
        tol = RPS_TOL_PCT * target_rps
        return (target_rps - tol) <= achieved_rps <= (target_rps + tol)
