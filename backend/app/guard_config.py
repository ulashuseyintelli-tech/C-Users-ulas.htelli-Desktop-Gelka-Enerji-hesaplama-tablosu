"""
Ops-Guard Configuration — centralized guard settings.

Loads from environment variables with OPS_GUARD_ prefix.
Invalid config → fallback to defaults + metric + WARNING log (NEVER reject).

Feature: ops-guard, Task 1.1
Feature: dependency-wrappers, Task 1.1
"""

import hashlib
import json
import logging
from enum import Enum
from typing import Optional

from pydantic import ValidationError, field_validator, model_validator
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
    cb_min_samples: int = 10  # minimum events in window before threshold applies

    # ── Dependency Wrapper — Global Flags (DW-1, DW-2, DW-3) ─────────────
    cb_precheck_enabled: bool = True          # DW-2: middleware CB pre-check flag
    wrapper_retry_on_write: bool = False       # DW-1: write path retry (default OFF)
    wrapper_fail_open_enabled: bool = True     # DW-3: wrapper internal error → fail-open

    # ── Dependency Wrapper — Timeout (seconds) ───────────────────────────
    wrapper_timeout_seconds_default: float = 5.0
    # Per-dependency override — JSON string: {"db_primary": 5.0, "external_api": 10.0, ...}
    # Only Dependency enum keys accepted; others silently ignored.
    wrapper_timeout_seconds_by_dependency: str = ""

    # ── Dependency Wrapper — Retry ───────────────────────────────────────
    wrapper_retry_max_attempts_default: int = 2       # max retries (total = 1 + this)
    wrapper_retry_backoff_base_ms: int = 500           # exponential backoff base
    wrapper_retry_backoff_cap_ms: int = 5000           # backoff cap
    wrapper_retry_jitter_pct: float = 0.2              # jitter as fraction of delay
    # Per-dependency override — JSON string: {"external_api": 3, "cache": 1, ...}
    wrapper_retry_max_attempts_by_dependency: str = ""

    # ── Guard Decision Layer (Feature: runtime-guard-decision) ───────────
    decision_layer_enabled: bool = False  # Explicit opt-in; default OFF
    # Mode: "shadow" = evaluate + metrics only (no block), "enforce" = full block
    decision_layer_mode: str = "shadow"

    # ── Tenant-Level Guard Decision Override (Feature: tenant-enable) ────
    decision_layer_default_mode: str = "shadow"  # "shadow"|"enforce"|"off"
    decision_layer_tenant_modes_json: str = ""    # JSON: {"tenantA":"enforce",...}
    decision_layer_tenant_allowlist_json: str = ""  # JSON: ["tenantA","tenantB"]
    # Endpoint → RiskClass eşlemesi (JSON): {"/admin/market-prices/upsert":"high",...}
    decision_layer_endpoint_risk_map_json: str = ""  # Empty → all endpoints LOW

    # ── Validators ────────────────────────────────────────────────────────

    @field_validator("wrapper_timeout_seconds_default")
    @classmethod
    def _validate_timeout_default(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"wrapper_timeout_seconds_default must be > 0, got {v}")
        return v

    @field_validator("wrapper_retry_max_attempts_default")
    @classmethod
    def _validate_retry_max_default(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"wrapper_retry_max_attempts_default must be >= 0, got {v}")
        return v

    @field_validator("wrapper_retry_backoff_base_ms")
    @classmethod
    def _validate_backoff_base(cls, v: int) -> int:
        if v <= 0:
            raise ValueError(f"wrapper_retry_backoff_base_ms must be > 0, got {v}")
        return v

    @field_validator("wrapper_retry_backoff_cap_ms")
    @classmethod
    def _validate_backoff_cap(cls, v: int) -> int:
        if v <= 0:
            raise ValueError(f"wrapper_retry_backoff_cap_ms must be > 0, got {v}")
        return v

    @field_validator("wrapper_retry_jitter_pct")
    @classmethod
    def _validate_jitter_pct(cls, v: float) -> float:
        if v < 0 or v > 1.0:
            raise ValueError(f"wrapper_retry_jitter_pct must be in [0, 1.0], got {v}")
        return v

    @field_validator("decision_layer_mode")
    @classmethod
    def _validate_decision_layer_mode(cls, v: str) -> str:
        allowed = {"shadow", "enforce"}
        if v not in allowed:
            raise ValueError(f"decision_layer_mode must be one of {allowed}, got {v!r}")
        return v

    @field_validator("decision_layer_default_mode")
    @classmethod
    def _validate_decision_layer_default_mode(cls, v: str) -> str:
        allowed = {"shadow", "enforce", "off"}
        if v not in allowed:
            raise ValueError(f"decision_layer_default_mode must be one of {allowed}, got {v!r}")
        return v

    @model_validator(mode="after")
    def _validate_backoff_monotonicity(self) -> "GuardConfig":
        """Cross-field: backoff_base_ms <= backoff_cap_ms."""
        if self.wrapper_retry_backoff_base_ms > self.wrapper_retry_backoff_cap_ms:
            raise ValueError(
                f"wrapper_retry_backoff_base_ms ({self.wrapper_retry_backoff_base_ms}) "
                f"must be <= wrapper_retry_backoff_cap_ms ({self.wrapper_retry_backoff_cap_ms})"
            )
        return self

    # ── Computed helpers ──────────────────────────────────────────────────

    def get_timeout_for_dependency(self, dependency_name: str) -> float:
        """
        Return timeout seconds for a dependency.
        Per-dependency override > default. Invalid JSON → default.
        Only Dependency enum values accepted as keys.
        """
        overrides = self._parse_dependency_dict(self.wrapper_timeout_seconds_by_dependency)
        val = overrides.get(dependency_name)
        if val is not None and isinstance(val, (int, float)) and val > 0:
            return float(val)
        return self.wrapper_timeout_seconds_default

    def get_retry_max_attempts_for_dependency(self, dependency_name: str) -> int:
        """
        Return max retry attempts for a dependency.
        Per-dependency override > default. Invalid JSON → default.
        Only Dependency enum values accepted as keys.
        """
        overrides = self._parse_dependency_dict(self.wrapper_retry_max_attempts_by_dependency)
        val = overrides.get(dependency_name)
        if val is not None and isinstance(val, int) and val >= 0:
            return val
        return self.wrapper_retry_max_attempts_default

    @staticmethod
    def _parse_dependency_dict(raw: str) -> dict:
        """Parse JSON string to dict, filtering to valid Dependency enum keys only.
        
        Invalid JSON or invalid values → fallback to empty dict + metric increment.
        This ensures GuardConfigInvalid alert fires on config drift.
        """
        if not raw or not raw.strip():
            return {}
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                logger.warning(f"[OPS-GUARD] Dependency override is not a dict: {raw!r}")
                _inc_config_fallback_metric()
                return {}
            # Filter to valid Dependency enum values only (HD-5)
            from .guards.circuit_breaker import Dependency
            valid_keys = {d.value for d in Dependency}
            filtered = {}
            for k, v in data.items():
                if k not in valid_keys:
                    logger.warning(f"[OPS-GUARD] Unknown dependency key in override: {k!r}")
                    _inc_config_fallback_metric()
                    continue
                filtered[k] = v
            return filtered
        except (json.JSONDecodeError, Exception) as exc:
            logger.warning(f"[OPS-GUARD] Failed to parse dependency override JSON: {raw!r} ({exc})")
            _inc_config_fallback_metric()
            return {}

    @property
    def config_hash(self) -> str:
        """Deterministic hash of current config for logging (HD-4)."""
        raw = f"{self.schema_version}:{self.config_version}:{self.last_updated_at}"
        return hashlib.sha256(raw.encode()).hexdigest()[:12]


def _inc_config_fallback_metric() -> None:
    """Increment guard config fallback metric. Safe to call anytime."""
    try:
        from .ptf_metrics import get_ptf_metrics
        get_ptf_metrics().inc_guard_config_fallback()
    except Exception:
        pass  # metrics not yet initialized


# ── Singleton with fallback ───────────────────────────────────────────────────

_guard_config: Optional[GuardConfig] = None

# Default values for fallback — single source of truth
_FALLBACK_DEFAULTS = dict(
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
    cb_min_samples=10,
    # Dependency wrapper defaults
    cb_precheck_enabled=True,
    wrapper_retry_on_write=False,
    wrapper_fail_open_enabled=True,
    wrapper_timeout_seconds_default=5.0,
    wrapper_timeout_seconds_by_dependency="",
    wrapper_retry_max_attempts_default=2,
    wrapper_retry_backoff_base_ms=500,
    wrapper_retry_backoff_cap_ms=5000,
    wrapper_retry_jitter_pct=0.2,
    wrapper_retry_max_attempts_by_dependency="",
    # Guard Decision Layer
    decision_layer_enabled=False,
    decision_layer_mode="shadow",
    # Tenant-Level Guard Decision Override
    decision_layer_default_mode="shadow",
    decision_layer_tenant_modes_json="",
    decision_layer_tenant_allowlist_json="",
)


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
        _guard_config = GuardConfig.model_construct(**_FALLBACK_DEFAULTS)
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
