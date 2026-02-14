"""
FaultInjector — test-only singleton for controlled fault injection.

Manages injection points with TTL-based auto-expiry (monotonic clock).
No production endpoints; accessed only via FaultInjector.get_instance()
in test fixtures.

Feature: fault-injection, Task 1.1
Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6
"""

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class InjectionPoint(str, Enum):
    """Five injection points — one per fault scenario (S1–S5)."""
    DB_TIMEOUT = "DB_TIMEOUT"
    EXTERNAL_5XX_BURST = "EXTERNAL_5XX_BURST"
    KILLSWITCH_TOGGLE = "KILLSWITCH_TOGGLE"
    RATE_LIMIT_SPIKE = "RATE_LIMIT_SPIKE"
    GUARD_INTERNAL_ERROR = "GUARD_INTERNAL_ERROR"


@dataclass
class InjectionState:
    """Per-point injection state with TTL tracking."""
    enabled: bool = False
    params: dict[str, Any] = field(default_factory=dict)
    enabled_at: float = 0.0       # monotonic timestamp
    ttl_seconds: float = 0.0      # 0 = no expiry


class FaultInjector:
    """
    Singleton fault injector — test only.

    Usage:
        injector = FaultInjector.get_instance()
        injector.enable(InjectionPoint.DB_TIMEOUT, params={"delay_seconds": 0.5}, ttl_seconds=10)
        if injector.is_enabled(InjectionPoint.DB_TIMEOUT):
            ...
        injector.disable(InjectionPoint.DB_TIMEOUT)

    Cleanup:
        FaultInjector.reset_instance()  # in test teardown
    """

    _instance: Optional["FaultInjector"] = None

    def __init__(self) -> None:
        self._points: dict[InjectionPoint, InjectionState] = {
            p: InjectionState() for p in InjectionPoint
        }

    @classmethod
    def get_instance(cls) -> "FaultInjector":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton — call in test teardown."""
        cls._instance = None

    def enable(
        self,
        point: InjectionPoint,
        params: dict[str, Any] | None = None,
        ttl_seconds: float = 120.0,
    ) -> None:
        """Enable injection at the given point with optional params and TTL."""
        state = self._points[point]
        state.enabled = True
        state.params = params or {}
        state.enabled_at = time.monotonic()
        state.ttl_seconds = ttl_seconds

    def disable(self, point: InjectionPoint) -> None:
        """Disable injection at the given point."""
        state = self._points[point]
        state.enabled = False
        state.params = {}

    def is_enabled(self, point: InjectionPoint) -> bool:
        """Check if injection is active (respects TTL auto-expiry)."""
        state = self._points[point]
        if not state.enabled:
            return False
        if state.ttl_seconds > 0 and (time.monotonic() - state.enabled_at) > state.ttl_seconds:
            state.enabled = False
            return False
        return True

    def get_params(self, point: InjectionPoint) -> dict[str, Any]:
        """Return params for the given injection point."""
        return self._points[point].params

    def disable_all(self) -> None:
        """Disable all injection points."""
        for point in InjectionPoint:
            self.disable(point)
