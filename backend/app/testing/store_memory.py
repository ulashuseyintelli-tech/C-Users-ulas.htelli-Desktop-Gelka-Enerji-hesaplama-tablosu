"""
PR-9: In-memory StateStore implementation.

Thread-safe via threading.Lock for multi-instance simulation.
Deterministic — no real IO.
"""
from __future__ import annotations

import threading
from typing import Optional

from .policy_engine import AuditEntry, Override
from .state_store import StateStore, DedupResult


class MemoryStateStore(StateStore):
    """
    In-memory shared state store with lock-based CAS.
    Suitable for testing and single-process multi-instance simulation.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: dict[str, str] = {}       # event_id → instance_id
        self._audit: list[AuditEntry] = []
        self._audit_keys: set[str] = set()       # idempotency keys
        self._overrides: dict[str, Override] = {}  # idempotency_key → Override

    # -- Dedup --

    def try_claim_event(self, event_id: str, instance_id: str) -> DedupResult:
        with self._lock:
            if event_id in self._events:
                return DedupResult(
                    event_id=event_id,
                    is_new=False,
                    owner_instance=self._events[event_id],
                )
            self._events[event_id] = instance_id
            return DedupResult(
                event_id=event_id,
                is_new=True,
                owner_instance=instance_id,
            )

    def is_claimed(self, event_id: str) -> bool:
        with self._lock:
            return event_id in self._events

    # -- Audit --

    def append_audit(self, entry: AuditEntry) -> bool:
        with self._lock:
            if entry.override and entry.override.idempotency_key:
                if entry.override.idempotency_key in self._audit_keys:
                    return False
                self._audit_keys.add(entry.override.idempotency_key)
            self._audit.append(entry)
            return True

    def query_audit(self, last_n: int = 50) -> list[AuditEntry]:
        with self._lock:
            return list(reversed(self._audit[-last_n:]))

    def audit_count(self) -> int:
        with self._lock:
            return len(self._audit)

    # -- Overrides --

    def put_override(self, override: Override) -> bool:
        with self._lock:
            if override.idempotency_key in self._overrides:
                return False
            self._overrides[override.idempotency_key] = override
            return True

    def get_active_overrides(self, now_ms: int) -> list[Override]:
        with self._lock:
            return [
                o for o in self._overrides.values()
                if not o.is_expired(now_ms)
            ]

    def expire_overrides(self, now_ms: int) -> int:
        with self._lock:
            expired_keys = [
                k for k, o in self._overrides.items()
                if o.is_expired(now_ms)
            ]
            for k in expired_keys:
                del self._overrides[k]
            return len(expired_keys)
