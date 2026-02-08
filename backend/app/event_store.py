"""
Counter-only event store for frontend telemetry.

Payload saklamaz — yalnızca per-event_name accepted/rejected sayaçları tutar.
PII/secret sızıntı riski yok çünkü properties hiçbir yerde persist edilmez.

Feature: telemetry-unification, Task 5.1
Requirements: 6.6
"""

import logging
import threading
from typing import Dict

logger = logging.getLogger(__name__)


class EventStore:
    """Thread-safe, counter-only event store (singleton pattern)."""

    def __init__(self) -> None:
        self._counters: Dict[str, int] = {}  # event_name → count
        self._total_accepted: int = 0
        self._total_rejected: int = 0
        self._lock = threading.Lock()

    def increment(self, event_name: str) -> None:
        """Increment counter for an accepted event."""
        with self._lock:
            self._counters[event_name] = self._counters.get(event_name, 0) + 1
            self._total_accepted += 1

    def increment_rejected(self) -> None:
        """Increment rejected counter."""
        with self._lock:
            self._total_rejected += 1

    def get_counters(self) -> Dict[str, int]:
        """Return copy of per-event_name counters."""
        with self._lock:
            return dict(self._counters)

    def get_totals(self) -> Dict[str, int]:
        """Return total accepted and rejected counts."""
        with self._lock:
            return {"accepted": self._total_accepted, "rejected": self._total_rejected}

    def reset(self) -> None:
        """Clear all counters (for testing)."""
        with self._lock:
            self._counters.clear()
            self._total_accepted = 0
            self._total_rejected = 0


_store = EventStore()


def get_event_store() -> EventStore:
    """Get singleton EventStore instance."""
    return _store
