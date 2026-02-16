"""
Runtime Guard Enforcement — pure decision function.

Evaluates a GuardDecisionSnapshot and produces a deterministic verdict.
No side effects, no IO, no HTTP response construction.

Decision tree:
  1. snapshot is None → ALLOW (fail-open)
  2. guard_deny_reason is not None → PASSTHROUGH (mevcut davranış korunur)
  3. derived_has_insufficient → BLOCK_INSUFFICIENT (503)
  4. derived_has_stale → BLOCK_STALE (503)
  5. else → ALLOW

Feature: runtime-guard-decision, Task 7
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from .guard_decision import GuardDecisionSnapshot


class EnforcementVerdict(str, Enum):
    """Enforcement outcome. Bounded — exactly 4 values."""
    ALLOW = "ALLOW"
    PASSTHROUGH = "PASSTHROUGH"
    BLOCK_STALE = "BLOCK_STALE"
    BLOCK_INSUFFICIENT = "BLOCK_INSUFFICIENT"


def evaluate(snapshot: Optional[GuardDecisionSnapshot]) -> EnforcementVerdict:
    """
    Pure enforcement function. Deterministic, no side effects.

    Args:
        snapshot: Immutable decision snapshot, or None (fail-open).

    Returns:
        EnforcementVerdict indicating what the middleware should do.
    """
    # Fail-open: no snapshot → allow (R6)
    if snapshot is None:
        return EnforcementVerdict.ALLOW

    # Existing guard deny → passthrough to current HTTP semantics (R1, R8.1)
    if snapshot.guard_deny_reason is not None:
        return EnforcementVerdict.PASSTHROUGH

    # Insufficient data → block (R8.2) — checked before stale (stricter)
    if snapshot.derived_has_insufficient:
        return EnforcementVerdict.BLOCK_INSUFFICIENT

    # Stale config → block (R8.3)
    if snapshot.derived_has_stale:
        return EnforcementVerdict.BLOCK_STALE

    # All clear (R8.4)
    return EnforcementVerdict.ALLOW
