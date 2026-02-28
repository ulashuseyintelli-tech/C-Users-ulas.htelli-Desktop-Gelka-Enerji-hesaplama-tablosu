"""Enforcement configuration (Phase F).

Config keys:
  INVOICE_VALIDATION_MODE         — off/shadow/enforce_soft/enforce_hard, default "shadow"
  INVOICE_VALIDATION_BLOCKER_CODES — comma-separated code names, default see _DEFAULT_BLOCKER_CODES
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum


class ValidationMode(str, Enum):
    OFF = "off"
    SHADOW = "shadow"
    ENFORCE_SOFT = "enforce_soft"
    ENFORCE_HARD = "enforce_hard"


class CodeSeverity(str, Enum):
    BLOCKER = "blocker"
    ADVISORY = "advisory"


_DEFAULT_BLOCKER_CODES: frozenset[str] = frozenset({
    "INVALID_ETTN",
    "INCONSISTENT_PERIODS",
    "REACTIVE_PENALTY_MISMATCH",
    "TOTAL_MISMATCH",
    "PAYABLE_TOTAL_MISMATCH",
})

_VALID_MODES = {m.value for m in ValidationMode}


@dataclass(frozen=True)
class EnforcementConfig:
    """Immutable enforcement config."""

    mode: ValidationMode = ValidationMode.SHADOW
    blocker_codes: frozenset[str] = _DEFAULT_BLOCKER_CODES


def load_enforcement_config() -> EnforcementConfig:
    """Read config from env vars with safe fallbacks."""
    raw_mode = os.environ.get("INVOICE_VALIDATION_MODE", "").strip().lower()
    if raw_mode in _VALID_MODES:
        mode = ValidationMode(raw_mode)
    else:
        mode = ValidationMode.SHADOW

    raw_codes = os.environ.get("INVOICE_VALIDATION_BLOCKER_CODES", "")
    if raw_codes.strip():
        blocker_codes = frozenset(s.strip() for s in raw_codes.split(",") if s.strip())
    else:
        blocker_codes = _DEFAULT_BLOCKER_CODES

    return EnforcementConfig(mode=mode, blocker_codes=blocker_codes)
