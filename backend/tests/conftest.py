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
