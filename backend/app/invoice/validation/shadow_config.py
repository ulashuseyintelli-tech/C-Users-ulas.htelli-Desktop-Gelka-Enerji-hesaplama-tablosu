"""Shadow validation configuration (Phase E).

Config keys:
  INVOICE_SHADOW_SAMPLE_RATE  — float 0.0–1.0, default 0.01
  INVOICE_SHADOW_WHITELIST    — comma-separated pattern names, default "missing_totals_skips"
"""

from __future__ import annotations

import hashlib
import os
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .shadow import ShadowCompareResult

_DEFAULT_SAMPLE_RATE = 0.01
_DEFAULT_WHITELIST = frozenset({"missing_totals_skips"})
_BUCKET_SIZE = 10_000


@dataclass(frozen=True)
class ShadowConfig:
    """Immutable shadow validation config."""

    sample_rate: float = _DEFAULT_SAMPLE_RATE
    whitelist: frozenset[str] = _DEFAULT_WHITELIST


def load_config() -> ShadowConfig:
    """Read config from env vars with safe fallbacks."""
    raw_rate = os.environ.get("INVOICE_SHADOW_SAMPLE_RATE", "")
    try:
        rate = float(raw_rate)
        rate = max(0.0, min(1.0, rate))  # clamp
    except (ValueError, TypeError):
        rate = _DEFAULT_SAMPLE_RATE

    raw_wl = os.environ.get("INVOICE_SHADOW_WHITELIST", "")
    if raw_wl.strip():
        whitelist = frozenset(s.strip() for s in raw_wl.split(",") if s.strip())
    else:
        whitelist = _DEFAULT_WHITELIST

    return ShadowConfig(sample_rate=rate, whitelist=whitelist)


def should_sample(invoice_id: str | None, rate: float) -> bool:
    """Deterministic sampling when invoice_id is available.

    Uses SHA-256 (not built-in hash) for cross-process stability.
    Fallback: random when invoice_id is None.
    """
    if rate <= 0.0:
        return False
    if rate >= 1.0:
        return True

    if invoice_id is not None:
        digest = hashlib.sha256(invoice_id.encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:4], "big") % _BUCKET_SIZE
        return bucket < int(rate * _BUCKET_SIZE)

    return random.random() < rate


# ---------------------------------------------------------------------------
# Whitelist pattern matching
# ---------------------------------------------------------------------------

# Known divergence patterns — each is a callable predicate.
_DIVERGENCE_PATTERNS: dict[str, object] = {}


def _match_missing_totals_skips(result: ShadowCompareResult) -> bool:
    """Old emits ZERO_CONSUMPTION when lines missing; new skips."""
    return (
        not result.valid_match
        and result.codes_only_old == frozenset({"ZERO_CONSUMPTION"})
        and len(result.codes_only_new) == 0
    )


_DIVERGENCE_PATTERNS["missing_totals_skips"] = _match_missing_totals_skips


def is_whitelisted(result: ShadowCompareResult, whitelist: frozenset[str]) -> bool:
    """Check if a mismatch matches any whitelisted divergence pattern."""
    if result.valid_match:
        return False  # no mismatch → nothing to whitelist

    for pattern_name in whitelist:
        matcher = _DIVERGENCE_PATTERNS.get(pattern_name)
        if matcher is not None and matcher(result):  # type: ignore[operator]
            return True
    return False
