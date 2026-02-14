"""
Guard Internal Error Hook — injects RuntimeError in middleware guard chain.

Called via test-time monkeypatch on OpsGuardMiddleware._evaluate_guards.
Production code is NOT modified.

Feature: fault-injection, Task 4.1
Requirements: 7.1, 7.3
"""


def maybe_inject_guard_error() -> None:
    """
    Check GUARD_INTERNAL_ERROR injection point; raise RuntimeError if active.

    No-op when injection is disabled.
    """
    from .fault_injection import FaultInjector, InjectionPoint

    injector = FaultInjector.get_instance()
    if not injector.is_enabled(InjectionPoint.GUARD_INTERNAL_ERROR):
        return

    raise RuntimeError("Injected guard internal error")
