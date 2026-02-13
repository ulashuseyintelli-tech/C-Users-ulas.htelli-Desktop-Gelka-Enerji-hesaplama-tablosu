"""
Ops-Guard Configuration — centralized guard settings.

Loads from environment variables with OPS_GUARD_ prefix.
Invalid config → fallback to defaults + metric + WARNING log (NEVER reject).

Feature: ops-guard, Task 1.1
"""

import hashlib
import logging
from enum import Enum
from typing import Optional

from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class GuardDenyReason(str, Enum):
    """Deterministic deny reason enum (HD-3)."""
    KILL_SWITCHED = "KILL_SWITCHED"
    RATE_LIMITED = "RATE_LIMITED"
    CIRCUIT_OPEN = "CIRCUIT_OPEN"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class GuardConfig(BaseSettings):
    """
    Ops-Guard configuration.

    All fields have safe defaults so the system works even without
    any OPS_GUARD_* env vars. Invalid config → fallback + metric (HD-4).
    """

    model_config = SettingsConfigDict(
        env_prefix="OPS_GUARD_",
        env_file=".env",
        extra="ignore",
    )

    # Versioning (HD-4)
    schema_version: str = "1.0"
    config_version: str = "default"
    last_updated_at: str = ""

    # SLO thresholds
    slo_availability_target: float = 0.995
    slo_p95_latency_ms: int = 300
    slo_p99_latency_ms: int = 800
    slo_import_p95_seconds: float = 30.0
    slo_import_reject_rate_max: float = 0.20

    # Kill-switch defaults (all passive at startup)
    killswitch_global_import_disabled: bool = False
    killswitch_degrade_mode: bool = False
    killswitch_disabled_tenants: str = ""

    # Rate limit (per endpoint category)
    rate_limit_import_per_minute: int = 10
    rate_limit_heavy_read_per_minute: int = 120
    rate_limit_default_per_minute: int = 60
    rate_limit_fail_closed: bool = True

    # Circuit breaker
    cb_error_threshold_pct: float = 50.0
    cb_open_duration_seconds: float = 30.0
    cb_half_open_max_requests: int = 3
    cb_window_seconds: float = 60.0

    @property
    def config_hash(self) -> str:
        """Deterministic hash of current config for logging (HD-4)."""
        raw = f"{self.schema_version}:{self.config_version}:{self.last_updated_at}"
        return hashlib.sha256(raw.encode()).hexdigest()[:12]


# ── Singleton with fallback ───────────────────────────────────────────────────

_guard_config: Optional[GuardConfig] = None


def load_guard_config() -> GuardConfig:
    """
    Load GuardConfig from env. On failure → fallback defaults + metric + log.
    NEVER reject (HD-4).
    """
    global _guard_config
    try:
        _guard_config = GuardConfig()
        logger.info(
            f"[OPS-GUARD] Config loaded: schema={_guard_config.schema_version} "
            f"version={_guard_config.config_version} hash={_guard_config.config_hash}"
        )
    except (ValidationError, Exception) as exc:
        logger.warning(f"[OPS-GUARD] Config load failed, using defaults: {exc}")
        _guard_config = GuardConfig.model_construct(
            schema_version="1.0",
            config_version="default",
            last_updated_at="",
            slo_availability_target=0.995,
            slo_p95_latency_ms=300,
            slo_p99_latency_ms=800,
            slo_import_p95_seconds=30.0,
            slo_import_reject_rate_max=0.20,
            killswitch_global_import_disabled=False,
            killswitch_degrade_mode=False,
            killswitch_disabled_tenants="",
            rate_limit_import_per_minute=10,
            rate_limit_heavy_read_per_minute=120,
            rate_limit_default_per_minute=60,
            rate_limit_fail_closed=True,
            cb_error_threshold_pct=50.0,
            cb_open_duration_seconds=30.0,
            cb_half_open_max_requests=3,
            cb_window_seconds=60.0,
        )
        # Emit fallback metric
        try:
            from .ptf_metrics import get_ptf_metrics
            get_ptf_metrics().inc_guard_config_fallback()
        except Exception:
            pass  # metrics not yet initialized
    return _guard_config


def get_guard_config() -> GuardConfig:
    """Get current GuardConfig singleton. Loads on first call."""
    global _guard_config
    if _guard_config is None:
        return load_guard_config()
    return _guard_config
