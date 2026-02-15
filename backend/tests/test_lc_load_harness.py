import time
import pytest
from backend.app.testing.load_harness import LoadHarness, DEFAULT_PROFILES
from backend.app.testing.lc_config import ProfileType


def _now_ms():
    return int(time.time() * 1000)


def test_min_requests_enforced():
    harness = LoadHarness(now_ms_fn=_now_ms)
    prof = DEFAULT_PROFILES[ProfileType.BASELINE]
    # executed below min → fail
    with pytest.raises(ValueError):
        harness.run_dry(prof, executed_requests=prof.min_requests - 1)


def test_loadresult_smoke():
    harness = LoadHarness(now_ms_fn=_now_ms)
    prof = DEFAULT_PROFILES[ProfileType.BASELINE]
    res = harness.run_dry(prof)
    assert res.executed_requests >= prof.min_requests
    assert res.planned_requests >= prof.min_requests
