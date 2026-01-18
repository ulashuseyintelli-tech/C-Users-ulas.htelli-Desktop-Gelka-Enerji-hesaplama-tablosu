"""
Config Module - Sprint 8.8

Tüm sistem threshold'ları için TEK KAYNAK.

KURAL: Hard-coded threshold yasak!
- Tüm threshold'lar bu dosyadan import edilmeli
- from .config import THRESHOLDS
- Grep gate: grep -rn "THRESHOLD\\s*=" --include="*.py" | grep -v config.py | grep -v test_

INVARIANTS (startup'ta validate edilir):
- I1: SEVERE_RATIO >= RATIO
- I2: SEVERE_ABSOLUTE >= ABSOLUTE
- I3: ROUNDING_RATIO < RATIO
- I4: MIN_UNIT_PRICE < MAX_UNIT_PRICE
- I5: MIN_DIST_PRICE < MAX_DIST_PRICE
- I6: HARD_STOP_DELTA >= SEVERE_RATIO * 100
- I7: Tüm threshold'lar > 0
- I8: 0 < LOW_CONFIDENCE < 1
"""

from dataclasses import dataclass
from typing import List, Tuple


# ═══════════════════════════════════════════════════════════════════════════════
# THRESHOLD DATACLASSES
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class MismatchThresholds:
    """
    Total mismatch detection thresholds.
    
    S2 Mismatch: ratio >= RATIO OR delta >= ABSOLUTE
    S1 Escalation: (ratio >= SEVERE_RATIO AND delta >= ABSOLUTE) OR delta >= SEVERE_ABSOLUTE
    Rounding Tolerance: delta < ROUNDING_DELTA AND ratio < ROUNDING_RATIO
    """
    RATIO: float = 0.05              # %5 → S2
    ABSOLUTE: float = 50.0           # 50 TL → S2
    SEVERE_RATIO: float = 0.20       # %20 → S1 escalation
    SEVERE_ABSOLUTE: float = 500.0   # 500 TL → S1 escalation
    ROUNDING_DELTA: float = 10.0     # TL - rounding tolerance
    ROUNDING_RATIO: float = 0.005    # %0.5 - rounding tolerance


@dataclass(frozen=True)
class DriftThresholds:
    """
    Drift detection thresholds (Triple Guard).
    
    Alarm koşulu (tüm koşullar AND):
    - curr_total >= MIN_SAMPLE
    - abs(curr_count - prev_count) >= MIN_ABSOLUTE_DELTA
    - prev_rate > 0 ise: curr_rate >= RATE_MULTIPLIER * prev_rate
    """
    MIN_SAMPLE: int = 20
    MIN_ABSOLUTE_DELTA: int = 5
    RATE_MULTIPLIER: float = 2.0
    TOP_OFFENDERS_MIN_INVOICES: int = 20


@dataclass(frozen=True)
class AlertThresholds:
    """Alert configuration thresholds."""
    BUG_REPORT_RATE: float = 0.10    # %10
    EXHAUSTED_RATE: float = 0.20     # %20
    STUCK_COUNT: int = 1
    RECOMPUTE_LIMIT: int = 1


@dataclass(frozen=True)
class RecoveryThresholds:
    """Recovery and retry thresholds."""
    STUCK_MINUTES: int = 10


@dataclass(frozen=True)
class ValidationThresholds:
    """
    Validation thresholds.
    
    Unit price range: MIN_UNIT_PRICE - MAX_UNIT_PRICE TL/kWh
    Distribution price range: MIN_DIST_PRICE - MAX_DIST_PRICE TL/kWh
    """
    LOW_CONFIDENCE: float = 0.6
    MIN_UNIT_PRICE: float = 0.5      # TL/kWh
    MAX_UNIT_PRICE: float = 15.0     # TL/kWh
    MIN_DIST_PRICE: float = 0.0      # TL/kWh
    MAX_DIST_PRICE: float = 5.0      # TL/kWh
    LINE_CONSISTENCY_TOLERANCE: float = 2.0   # %2
    HARD_STOP_DELTA: float = 20.0    # %20
    ENERGY_CROSSCHECK_TOLERANCE: float = 5.0  # %5


@dataclass(frozen=True)
class FeedbackThresholds:
    """Feedback validation thresholds."""
    ROOT_CAUSE_MAX_LENGTH: int = 200


@dataclass(frozen=True)
class Thresholds:
    """
    All system thresholds - SINGLE SOURCE OF TRUTH.
    
    Usage:
        from .config import THRESHOLDS
        
        if ratio >= THRESHOLDS.Mismatch.RATIO:
            ...
    """
    Mismatch: MismatchThresholds = MismatchThresholds()
    Drift: DriftThresholds = DriftThresholds()
    Alert: AlertThresholds = AlertThresholds()
    Recovery: RecoveryThresholds = RecoveryThresholds()
    Validation: ValidationThresholds = ValidationThresholds()
    Feedback: FeedbackThresholds = FeedbackThresholds()


# Singleton instance - import this!
THRESHOLDS = Thresholds()


# ═══════════════════════════════════════════════════════════════════════════════
# ENVIRONMENT CONFIG
# ═══════════════════════════════════════════════════════════════════════════════


VALID_ENVIRONMENTS = frozenset({"development", "staging", "production"})


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIG VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════


class ConfigValidationError(Exception):
    """Raised when config validation fails at startup."""
    pass


def validate_config(thresholds: Thresholds = THRESHOLDS) -> None:
    """
    Validate config invariants at startup.
    
    MUST be called in startup_event() before accepting traffic.
    
    Raises:
        ConfigValidationError: If any invariant is violated
    
    Invariants:
        I1: SEVERE_RATIO >= RATIO
        I2: SEVERE_ABSOLUTE >= ABSOLUTE
        I3: ROUNDING_RATIO < RATIO
        I4: MIN_UNIT_PRICE < MAX_UNIT_PRICE
        I5: MIN_DIST_PRICE < MAX_DIST_PRICE
        I6: HARD_STOP_DELTA >= SEVERE_RATIO * 100
        I7: All thresholds > 0
        I8: 0 < LOW_CONFIDENCE < 1
    """
    errors: List[str] = []
    m = thresholds.Mismatch
    v = thresholds.Validation
    d = thresholds.Drift
    r = thresholds.Recovery
    
    # I1: Severe thresholds >= normal thresholds (ratio)
    if m.SEVERE_RATIO < m.RATIO:
        errors.append(
            f"I1 FAIL: SEVERE_RATIO ({m.SEVERE_RATIO}) must be >= RATIO ({m.RATIO})"
        )
    
    # I2: Severe thresholds >= normal thresholds (absolute)
    if m.SEVERE_ABSOLUTE < m.ABSOLUTE:
        errors.append(
            f"I2 FAIL: SEVERE_ABSOLUTE ({m.SEVERE_ABSOLUTE}) must be >= ABSOLUTE ({m.ABSOLUTE})"
        )
    
    # I3: Rounding threshold < mismatch threshold
    if m.ROUNDING_RATIO >= m.RATIO:
        errors.append(
            f"I3 FAIL: ROUNDING_RATIO ({m.ROUNDING_RATIO}) must be < RATIO ({m.RATIO}) "
            "to prevent rounding from swallowing real mismatches"
        )
    
    # I4: Min < Max for unit price range
    if v.MIN_UNIT_PRICE >= v.MAX_UNIT_PRICE:
        errors.append(
            f"I4 FAIL: MIN_UNIT_PRICE ({v.MIN_UNIT_PRICE}) must be < MAX_UNIT_PRICE ({v.MAX_UNIT_PRICE})"
        )
    
    # I5: Min < Max for distribution price range
    if v.MIN_DIST_PRICE >= v.MAX_DIST_PRICE:
        errors.append(
            f"I5 FAIL: MIN_DIST_PRICE ({v.MIN_DIST_PRICE}) must be < MAX_DIST_PRICE ({v.MAX_DIST_PRICE})"
        )
    
    # I6: Hard stop >= severe ratio (avoid conflicting alarms)
    if v.HARD_STOP_DELTA < m.SEVERE_RATIO * 100:
        errors.append(
            f"I6 FAIL: HARD_STOP_DELTA ({v.HARD_STOP_DELTA}%) must be >= "
            f"SEVERE_RATIO ({m.SEVERE_RATIO * 100}%) to avoid conflicting alarms"
        )
    
    # I7: All positive values
    positive_checks: List[Tuple[str, float]] = [
        ("Mismatch.RATIO", m.RATIO),
        ("Mismatch.ABSOLUTE", m.ABSOLUTE),
        ("Mismatch.ROUNDING_DELTA", m.ROUNDING_DELTA),
        ("Mismatch.ROUNDING_RATIO", m.ROUNDING_RATIO),
        ("Drift.MIN_SAMPLE", d.MIN_SAMPLE),
        ("Drift.MIN_ABSOLUTE_DELTA", d.MIN_ABSOLUTE_DELTA),
        ("Recovery.STUCK_MINUTES", r.STUCK_MINUTES),
        ("Validation.LOW_CONFIDENCE", v.LOW_CONFIDENCE),
    ]
    for name, value in positive_checks:
        if value <= 0:
            errors.append(f"I7 FAIL: {name} ({value}) must be > 0")
    
    # I8: Confidence in valid range (0, 1)
    if not (0 < v.LOW_CONFIDENCE < 1):
        errors.append(
            f"I8 FAIL: LOW_CONFIDENCE ({v.LOW_CONFIDENCE}) must be in range (0, 1)"
        )
    
    if errors:
        raise ConfigValidationError(
            f"Config validation failed with {len(errors)} error(s):\n" +
            "\n".join(f"  - {e}" for e in errors)
        )


def get_config_summary() -> dict:
    """
    Get config summary for /health/ready endpoint.
    
    Returns:
        Dict with all threshold values for debugging
    """
    return {
        "mismatch": {
            "ratio": THRESHOLDS.Mismatch.RATIO,
            "absolute": THRESHOLDS.Mismatch.ABSOLUTE,
            "severe_ratio": THRESHOLDS.Mismatch.SEVERE_RATIO,
            "severe_absolute": THRESHOLDS.Mismatch.SEVERE_ABSOLUTE,
            "rounding_delta": THRESHOLDS.Mismatch.ROUNDING_DELTA,
            "rounding_ratio": THRESHOLDS.Mismatch.ROUNDING_RATIO,
        },
        "drift": {
            "min_sample": THRESHOLDS.Drift.MIN_SAMPLE,
            "min_absolute_delta": THRESHOLDS.Drift.MIN_ABSOLUTE_DELTA,
            "rate_multiplier": THRESHOLDS.Drift.RATE_MULTIPLIER,
            "top_offenders_min_invoices": THRESHOLDS.Drift.TOP_OFFENDERS_MIN_INVOICES,
        },
        "alert": {
            "bug_report_rate": THRESHOLDS.Alert.BUG_REPORT_RATE,
            "exhausted_rate": THRESHOLDS.Alert.EXHAUSTED_RATE,
            "stuck_count": THRESHOLDS.Alert.STUCK_COUNT,
            "recompute_limit": THRESHOLDS.Alert.RECOMPUTE_LIMIT,
        },
        "recovery": {
            "stuck_minutes": THRESHOLDS.Recovery.STUCK_MINUTES,
        },
        "validation": {
            "low_confidence": THRESHOLDS.Validation.LOW_CONFIDENCE,
            "min_unit_price": THRESHOLDS.Validation.MIN_UNIT_PRICE,
            "max_unit_price": THRESHOLDS.Validation.MAX_UNIT_PRICE,
            "min_dist_price": THRESHOLDS.Validation.MIN_DIST_PRICE,
            "max_dist_price": THRESHOLDS.Validation.MAX_DIST_PRICE,
            "hard_stop_delta": THRESHOLDS.Validation.HARD_STOP_DELTA,
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# SPRINT 8.9: CONFIG HASH FOR VERSION TRACKING
# ═══════════════════════════════════════════════════════════════════════════════


def get_config_hash() -> str:
    """
    Calculate SHA256 hash of config for version tracking.
    
    Used in:
    - Startup log
    - /health/ready response
    - Run summary
    
    Returns:
        First 16 chars of SHA256 hash with prefix
    """
    import hashlib
    import json
    
    summary = get_config_summary()
    config_json = json.dumps(summary, sort_keys=True)
    full_hash = hashlib.sha256(config_json.encode()).hexdigest()
    return f"sha256:{full_hash[:16]}"
