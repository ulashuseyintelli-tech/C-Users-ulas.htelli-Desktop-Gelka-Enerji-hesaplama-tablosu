"""
DB Timeout Hook — injects TimeoutError in DB call path.

Called via test-time monkeypatch; production code is NOT modified.
When DB_TIMEOUT injection is active, raises TimeoutError with
optional delay simulation.

Feature: fault-injection, Task 2.2
Requirements: 2.4, 2.5
"""

import time


def maybe_inject_db_timeout() -> None:
    """
    Check DB_TIMEOUT injection point; raise TimeoutError if active.

    No-op when injection is disabled.
    """
    from .fault_injection import FaultInjector, InjectionPoint

    injector = FaultInjector.get_instance()
    if not injector.is_enabled(InjectionPoint.DB_TIMEOUT):
        return

    params = injector.get_params(InjectionPoint.DB_TIMEOUT)
    delay = params.get("delay_seconds", 0)
    if delay > 0:
        time.sleep(delay)
    raise TimeoutError(f"Injected DB timeout (delay={delay}s)")
