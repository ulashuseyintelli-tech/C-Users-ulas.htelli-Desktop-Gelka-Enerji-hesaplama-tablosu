"""
Shared test configuration for backend tests.

Hypothesis settings:
- CI profile disables example database to prevent "Flaky" errors from stale examples
- Default profile keeps database for local development
"""

from hypothesis import settings, HealthCheck

# CI profile: no example database → no stale example → no Flaky errors
settings.register_profile(
    "ci",
    database=None,
    suppress_health_check=[HealthCheck.too_slow],
)

# Default profile: keep database, suppress slow health check
settings.register_profile(
    "default",
    suppress_health_check=[HealthCheck.too_slow],
)

settings.load_profile("default")


# ── PR-10: Test tier markers ──────────────────────────────────────────────────
# Usage: pytest -m smoke, pytest -m core, pytest -m concurrency
# These are registered to avoid PytestUnknownMarkWarning.

def pytest_configure(config):
    config.addinivalue_line("markers", "smoke: Tier-0 pure-math + config tests (<10s)")
    config.addinivalue_line("markers", "core: Tier-1 core logic + stores (<15s)")
    config.addinivalue_line("markers", "concurrency: Tier-2 thread races (<30s)")
    config.addinivalue_line("markers", "soak: Tier-3 large PBT / nightly (<120s)")


# ── Ops-Guard singleton isolation ─────────────────────────────────────────────
# Rate limiter and kill-switch singletons are module-level; without reset,
# tests that share the same process accumulate state (e.g. rate limit buckets
# fill up across test files). This autouse fixture resets them before each test.

import pytest


@pytest.fixture(autouse=True)
def _reset_ops_guard_singletons():
    """Reset ops-guard singletons before each test for isolation."""
    try:
        import app.ops_guard_middleware as ogm
        if ogm._rate_limit_guard is not None:
            ogm._rate_limit_guard.reset()
    except Exception:
        pass

    try:
        import app.main as main_mod
        if main_mod._kill_switch_manager is not None:
            # Re-init from config (resets switch states)
            pass  # kill-switch state is test-managed, don't auto-reset
    except Exception:
        pass

    yield

    # Post-test cleanup
    try:
        import app.ops_guard_middleware as ogm
        if ogm._rate_limit_guard is not None:
            ogm._rate_limit_guard.reset()
    except Exception:
        pass
