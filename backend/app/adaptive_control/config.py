"""
Adaptive Control Configuration — config, validation, allowlist, canonical SLO signals.

Loads from environment variables with ADAPTIVE_CONTROL_ prefix.
Invalid config → validate() returns errors, mevcut config korunur.

Feature: slo-adaptive-control, Tasks 1.1, 1.2, 1.3
Requirements: 2.1–2.5, 5.1, 9.1–9.7, CC.1, CC.5
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


# ── Canonical SLO Signals (v1 binding) ────────────────────────────────────────
# These are the single canonical signals for v1. Changing them requires
# a new config revision + test gate (Req 2.2, 2.3, 2.4).

CANONICAL_GUARD_SLO_QUERY = (
    "histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m]))"
)
CANONICAL_PDF_SLO_QUERY = (
    "histogram_quantile(0.95, rate(ptf_admin_pdf_render_total_seconds_bucket[5m]))"
)


# ── AllowlistEntry + AllowlistManager (Task 1.2) ─────────────────────────────

@dataclass(frozen=True)
class AllowlistEntry:
    """A single allowlist target. Frozen for hashability and safety."""
    tenant_id: str = "*"
    endpoint_class: str = "*"
    subsystem_id: str = "*"


class AllowlistManager:
    """
    Manages the set of targets adaptive control can act upon.
    Empty allowlist → zero actions on any target (Req 9.6).
    """

    def __init__(self, entries: Optional[list[AllowlistEntry]] = None) -> None:
        self._entries: list[AllowlistEntry] = list(entries) if entries else []

    @property
    def is_empty(self) -> bool:
        return len(self._entries) == 0

    @property
    def entries(self) -> list[AllowlistEntry]:
        return list(self._entries)

    def is_in_scope(
        self,
        tenant_id: str = "*",
        endpoint_class: str = "*",
        subsystem_id: str = "*",
    ) -> bool:
        """Check if a target is within the allowlist scope.

        Wildcard '*' in an entry field matches any value.
        Empty allowlist → always False (Req 9.6, CC.5).
        """
        if self.is_empty:
            return False
        for entry in self._entries:
            tenant_match = entry.tenant_id == "*" or entry.tenant_id == tenant_id
            endpoint_match = entry.endpoint_class == "*" or entry.endpoint_class == endpoint_class
            subsystem_match = entry.subsystem_id == "*" or entry.subsystem_id == subsystem_id
            if tenant_match and endpoint_match and subsystem_match:
                return True
        return False

    def update(
        self,
        new_entries: list[AllowlistEntry],
        actor: str = "system",
    ) -> dict:
        """Replace allowlist entries. Returns audit record (Req 9.7)."""
        now = datetime.now(timezone.utc).isoformat()
        old_entries = self._entries
        self._entries = list(new_entries)
        audit = {
            "action": "allowlist_update",
            "old_entries": [_entry_to_dict(e) for e in old_entries],
            "new_entries": [_entry_to_dict(e) for e in new_entries],
            "actor": actor,
            "timestamp": now,
        }
        logger.info(f"[ADAPTIVE-CONTROL] Allowlist updated: {json.dumps(audit)}")
        return audit


def _entry_to_dict(entry: AllowlistEntry) -> dict:
    return {
        "tenant_id": entry.tenant_id,
        "endpoint_class": entry.endpoint_class,
        "subsystem_id": entry.subsystem_id,
    }


# ── AdaptiveControlConfig (Task 1.1) ─────────────────────────────────────────

@dataclass
class AdaptiveControlConfig:
    """
    All adaptive control parameters. Pure dataclass — no env loading here.
    Validation via validate() method (Req 9.2).
    """

    # Control loop
    control_loop_interval_seconds: float = 30.0

    # Guard thresholds (p95 latency in seconds)
    p95_latency_enter_threshold: float = 0.5
    p95_latency_exit_threshold: float = 0.3

    # PDF queue depth thresholds
    queue_depth_enter_threshold: int = 50
    queue_depth_exit_threshold: int = 20

    # Error budget
    error_budget_window_seconds: int = 30 * 86400  # 30 days rolling
    guard_slo_target: float = 0.999
    pdf_slo_target: float = 0.999
    burn_rate_threshold: float = 1.0

    # Hysteresis / oscillation
    dwell_time_seconds: float = 600.0  # 10 minutes
    cooldown_period_seconds: float = 300.0  # 5 minutes
    oscillation_window_size: int = 10
    oscillation_max_transitions: int = 4

    # Telemetry sufficiency
    min_sample_ratio: float = 0.8
    min_bucket_coverage_pct: float = 80.0

    # Canonical SLO queries (v1 binding)
    guard_slo_query: str = CANONICAL_GUARD_SLO_QUERY
    pdf_slo_query: str = CANONICAL_PDF_SLO_QUERY

    # Allowlist
    targets: list[AllowlistEntry] = field(default_factory=list)

    def validate(self) -> list[str]:
        """Validate config. Returns list of error strings. Empty = valid (Req 9.2)."""
        errors: list[str] = []

        # Exit threshold must be < enter threshold (hysteresis band)
        if self.p95_latency_exit_threshold >= self.p95_latency_enter_threshold:
            errors.append(
                f"p95_latency_exit_threshold ({self.p95_latency_exit_threshold}) "
                f"must be < p95_latency_enter_threshold ({self.p95_latency_enter_threshold})"
            )
        if self.queue_depth_exit_threshold >= self.queue_depth_enter_threshold:
            errors.append(
                f"queue_depth_exit_threshold ({self.queue_depth_exit_threshold}) "
                f"must be < queue_depth_enter_threshold ({self.queue_depth_enter_threshold})"
            )

        # SLO targets in (0, 1]
        for name, val in [
            ("guard_slo_target", self.guard_slo_target),
            ("pdf_slo_target", self.pdf_slo_target),
        ]:
            if not (0.0 < val <= 1.0):
                errors.append(f"{name} ({val}) must be in (0, 1]")

        # Positive durations
        for name, val in [
            ("control_loop_interval_seconds", self.control_loop_interval_seconds),
            ("dwell_time_seconds", self.dwell_time_seconds),
            ("cooldown_period_seconds", self.cooldown_period_seconds),
        ]:
            if val <= 0:
                errors.append(f"{name} ({val}) must be > 0")

        # Positive error budget window
        if self.error_budget_window_seconds <= 0:
            errors.append(
                f"error_budget_window_seconds ({self.error_budget_window_seconds}) must be > 0"
            )

        # Burn rate > 0
        if self.burn_rate_threshold <= 0:
            errors.append(f"burn_rate_threshold ({self.burn_rate_threshold}) must be > 0")

        # Positive thresholds
        if self.p95_latency_enter_threshold <= 0:
            errors.append(
                f"p95_latency_enter_threshold ({self.p95_latency_enter_threshold}) must be > 0"
            )
        if self.queue_depth_enter_threshold <= 0:
            errors.append(
                f"queue_depth_enter_threshold ({self.queue_depth_enter_threshold}) must be > 0"
            )

        # Oscillation params
        if self.oscillation_window_size <= 0:
            errors.append(
                f"oscillation_window_size ({self.oscillation_window_size}) must be > 0"
            )
        if self.oscillation_max_transitions <= 0:
            errors.append(
                f"oscillation_max_transitions ({self.oscillation_max_transitions}) must be > 0"
            )

        # Sufficiency params
        if not (0.0 < self.min_sample_ratio <= 1.0):
            errors.append(f"min_sample_ratio ({self.min_sample_ratio}) must be in (0, 1]")
        if not (0.0 < self.min_bucket_coverage_pct <= 100.0):
            errors.append(
                f"min_bucket_coverage_pct ({self.min_bucket_coverage_pct}) must be in (0, 100]"
            )

        return errors


# ── Config Drift Detection (Task 1.3) ────────────────────────────────────────

def check_config_drift(config: AdaptiveControlConfig) -> Optional[str]:
    """Check if SLO query parameters match canonical definitions.

    Returns None if OK, error string if drift detected (Req 2.5).
    """
    if config.guard_slo_query != CANONICAL_GUARD_SLO_QUERY:
        return (
            f"config_drift_detected: guard_slo_query "
            f"'{config.guard_slo_query}' != canonical '{CANONICAL_GUARD_SLO_QUERY}'"
        )
    if config.pdf_slo_query != CANONICAL_PDF_SLO_QUERY:
        return (
            f"config_drift_detected: pdf_slo_query "
            f"'{config.pdf_slo_query}' != canonical '{CANONICAL_PDF_SLO_QUERY}'"
        )
    return None


# ── Fallback Defaults + Env Loading (Task 1.1) ───────────────────────────────

_FALLBACK_DEFAULTS = dict(
    control_loop_interval_seconds=30.0,
    p95_latency_enter_threshold=0.5,
    p95_latency_exit_threshold=0.3,
    queue_depth_enter_threshold=50,
    queue_depth_exit_threshold=20,
    error_budget_window_seconds=30 * 86400,
    guard_slo_target=0.999,
    pdf_slo_target=0.999,
    burn_rate_threshold=1.0,
    dwell_time_seconds=600.0,
    cooldown_period_seconds=300.0,
    oscillation_window_size=10,
    oscillation_max_transitions=4,
    min_sample_ratio=0.8,
    min_bucket_coverage_pct=80.0,
    guard_slo_query=CANONICAL_GUARD_SLO_QUERY,
    pdf_slo_query=CANONICAL_PDF_SLO_QUERY,
    targets=[],
)

# Env var prefix
_ENV_PREFIX = "ADAPTIVE_CONTROL_"

# Mapping: env var suffix → (field_name, type_converter)
_ENV_MAP: dict[str, tuple[str, type]] = {
    "LOOP_INTERVAL": ("control_loop_interval_seconds", float),
    "P95_LATENCY_ENTER": ("p95_latency_enter_threshold", float),
    "P95_LATENCY_EXIT": ("p95_latency_exit_threshold", float),
    "QUEUE_DEPTH_ENTER": ("queue_depth_enter_threshold", int),
    "QUEUE_DEPTH_EXIT": ("queue_depth_exit_threshold", int),
    "BUDGET_WINDOW": ("error_budget_window_seconds", int),
    "GUARD_SLO_TARGET": ("guard_slo_target", float),
    "PDF_SLO_TARGET": ("pdf_slo_target", float),
    "BURN_RATE_THRESHOLD": ("burn_rate_threshold", float),
    "DWELL_TIME": ("dwell_time_seconds", float),
    "COOLDOWN_PERIOD": ("cooldown_period_seconds", float),
    "OSCILLATION_WINDOW": ("oscillation_window_size", int),
    "OSCILLATION_MAX_TRANSITIONS": ("oscillation_max_transitions", int),
    "MIN_SAMPLE_RATIO": ("min_sample_ratio", float),
    "MIN_BUCKET_COVERAGE": ("min_bucket_coverage_pct", float),
}


def load_adaptive_control_config(
    env: Optional[dict[str, str]] = None,
) -> AdaptiveControlConfig:
    """Load AdaptiveControlConfig from environment variables.

    Pattern consistent with GuardConfig (Req 9.4).
    Invalid env values → fallback to defaults + log warning.
    """
    import os

    source = env if env is not None else os.environ
    kwargs: dict = dict(_FALLBACK_DEFAULTS)

    for suffix, (field_name, converter) in _ENV_MAP.items():
        env_key = f"{_ENV_PREFIX}{suffix}"
        raw = source.get(env_key)
        if raw is not None:
            try:
                kwargs[field_name] = converter(raw)
            except (ValueError, TypeError) as exc:
                logger.warning(
                    f"[ADAPTIVE-CONTROL] Invalid env {env_key}={raw!r}: {exc}, "
                    f"using default {_FALLBACK_DEFAULTS[field_name]}"
                )

    # Allowlist from JSON env var
    targets_raw = source.get(f"{_ENV_PREFIX}TARGETS_JSON")
    if targets_raw:
        try:
            targets_data = json.loads(targets_raw)
            if isinstance(targets_data, list):
                kwargs["targets"] = [
                    AllowlistEntry(
                        tenant_id=t.get("tenant_id", "*"),
                        endpoint_class=t.get("endpoint_class", "*"),
                        subsystem_id=t.get("subsystem_id", "*"),
                    )
                    for t in targets_data
                    if isinstance(t, dict)
                ]
        except (json.JSONDecodeError, Exception) as exc:
            logger.warning(
                f"[ADAPTIVE-CONTROL] Invalid TARGETS_JSON: {exc}, using empty allowlist"
            )

    config = AdaptiveControlConfig(**kwargs)

    # Validate and warn (but don't reject — return config with warnings)
    errors = config.validate()
    if errors:
        logger.warning(
            f"[ADAPTIVE-CONTROL] Config validation warnings: {errors}. "
            f"Falling back to safe defaults."
        )
        config = AdaptiveControlConfig(**_FALLBACK_DEFAULTS)

    # Config drift check
    drift = check_config_drift(config)
    if drift:
        logger.warning(f"[ADAPTIVE-CONTROL] {drift}")

    logger.info(
        f"[ADAPTIVE-CONTROL] Config loaded: loop_interval={config.control_loop_interval_seconds}s, "
        f"guard_enter={config.p95_latency_enter_threshold}, "
        f"queue_enter={config.queue_depth_enter_threshold}, "
        f"targets={len(config.targets)}"
    )
    return config
