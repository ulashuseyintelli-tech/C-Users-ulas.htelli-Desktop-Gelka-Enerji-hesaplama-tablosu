"""
PR-9: Shared State Store — backend-agnostic interface.

Provides:
- EventDedup: exactly-once event processing (unique constraint on event_id)
- AuditStore: append-only, queryable audit log
- OverrideStore: scoped overrides with TTL enforcement

All operations use CAS / transactional semantics where needed.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Optional

from .policy_engine import AuditEntry, Override


# ---------------------------------------------------------------------------
# Dedup result
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DedupResult:
    event_id: str
    is_new: bool          # True = first time, False = duplicate
    owner_instance: str   # which instance claimed it


# ---------------------------------------------------------------------------
# Store interface (abstract)
# ---------------------------------------------------------------------------

class StateStore(abc.ABC):
    """
    Backend-agnostic shared state store.
    Implementations must guarantee:
    - event_id uniqueness (exactly-once dedup)
    - audit append-only (no deletes/updates)
    - override TTL server-side enforcement
    """

    # -- Dedup --
    @abc.abstractmethod
    def try_claim_event(self, event_id: str, instance_id: str) -> DedupResult:
        """
        Attempt to claim an event_id for processing.
        Returns DedupResult with is_new=True if this is the first claim.
        Must be atomic / CAS — concurrent calls for same event_id:
        exactly one gets is_new=True, rest get is_new=False.
        """
        ...

    @abc.abstractmethod
    def is_claimed(self, event_id: str) -> bool:
        """Check if event_id has been claimed."""
        ...

    # -- Audit --
    @abc.abstractmethod
    def append_audit(self, entry: AuditEntry) -> bool:
        """
        Append an audit entry. Returns True on success.
        If entry has an override with idempotency_key, reject duplicates.
        """
        ...

    @abc.abstractmethod
    def query_audit(self, last_n: int = 50) -> list[AuditEntry]:
        """Return the last N audit entries (most recent first)."""
        ...

    @abc.abstractmethod
    def audit_count(self) -> int:
        """Total number of audit entries."""
        ...

    # -- Overrides --
    @abc.abstractmethod
    def put_override(self, override: Override) -> bool:
        """
        Store an override. Returns False if idempotency_key already exists.
        """
        ...

    @abc.abstractmethod
    def get_active_overrides(self, now_ms: int) -> list[Override]:
        """Return all overrides that have not expired as of now_ms."""
        ...

    @abc.abstractmethod
    def expire_overrides(self, now_ms: int) -> int:
        """Remove expired overrides. Returns count of removed."""
        ...
