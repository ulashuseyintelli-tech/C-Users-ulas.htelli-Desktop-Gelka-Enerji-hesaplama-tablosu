from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Final


DEFAULT_SEED: Final[int] = 1337
RPS_TOL_PCT: Final[float] = 0.30  # ±30%

# Runtime truth: do NOT treat as spec param.
EVAL_INTERVAL_SECONDS: Final[int] = int(os.getenv("EVAL_INTERVAL_SECONDS", "60"))


class ProfileType(str, Enum):
    BASELINE = "baseline"
    PEAK = "peak"
    STRESS = "stress"
    BURST = "burst"


class FaultType(str, Enum):
    """PR-2: Fault types for failure matrix scenarios FM-1..FM-5."""
    DB_TIMEOUT = "db_timeout"
    EXTERNAL_5XX = "external_5xx"
    KILLSWITCH = "killswitch"
    RATE_LIMIT = "rate_limit"
    GUARD_ERROR = "guard_error"


# FM scenario → expected CB behavior at 100% failure rate
FM_EXPECTS_CB_OPEN: Final[dict[FaultType, bool]] = {
    FaultType.DB_TIMEOUT: True,
    FaultType.EXTERNAL_5XX: True,
    FaultType.KILLSWITCH: False,   # killswitch bypasses CB
    FaultType.RATE_LIMIT: False,   # rate limit is pre-CB
    FaultType.GUARD_ERROR: True,
}


# GNK-3 minimum request counts by profile
MIN_REQUESTS_BY_PROFILE: Final[dict[ProfileType, int]] = {
    ProfileType.BASELINE: 200,
    ProfileType.PEAK: 200,
    ProfileType.STRESS: 500,
    ProfileType.BURST: 500,
}


def retry_amp_tolerance(expected: float) -> float:
    """
    Relative+absolute tolerance used by R2 AC4 for retry amplification comparisons.
    """
    return max(1e-6, 1e-4 * abs(expected))


@dataclass(frozen=True)
class LcRuntimeConfig:
    """
    Cross-cutting runtime configuration for LC tests. Keep minimal in PR-1.
    """
    seed: int = DEFAULT_SEED
    eval_interval_seconds: int = EVAL_INTERVAL_SECONDS
