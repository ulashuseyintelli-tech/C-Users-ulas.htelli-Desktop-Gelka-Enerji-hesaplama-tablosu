"""
Runtime Guard Decision Layer — signal model, snapshot factory, hash.

Per-request immutable decision snapshot. Mevcut guard zincirini
(KillSwitch → RateLimiter → CircuitBreaker) wrap eder, replace etmez.
Guard çıktıları "signal" olarak normalize edilir; config freshness ve
CB mapping sufficiency kontrol edilir.

Snapshot frozen dataclass: üretildikten sonra değiştirilemez.
SnapshotFactory.build() fail-open: exception → None + log.

Feature: runtime-guard-decision, Task 1–6
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from ..guard_config import GuardConfig, GuardDenyReason

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# Enums — bounded cardinality (HD-5)
# ═══════════════════════════════════════════════════════════════════════════════

class SignalStatus(str, Enum):
    """Guard signal health status. Exactly 3 values — bounded."""
    OK = "OK"
    STALE = "STALE"
    INSUFFICIENT = "INSUFFICIENT"


class SignalName(str, Enum):
    """Signal source identifier. Bounded set."""
    CONFIG_FRESHNESS = "CONFIG_FRESHNESS"
    CB_MAPPING = "CB_MAPPING"


class SignalReasonCode(str, Enum):
    """Reason code for signal status. Bounded set."""
    OK = "OK"
    CONFIG_TIMESTAMP_MISSING = "CONFIG_TIMESTAMP_MISSING"
    CONFIG_TIMESTAMP_PARSE_ERROR = "CONFIG_TIMESTAMP_PARSE_ERROR"
    CONFIG_STALE = "CONFIG_STALE"
    CB_MAPPING_MISS = "CB_MAPPING_MISS"



class TenantMode(str, Enum):
    """Tenant-level guard decision layer operating mode."""
    SHADOW = "shadow"
    ENFORCE = "enforce"
    OFF = "off"


# ═══════════════════════════════════════════════════════════════════════════════
# Config parse helpers — fail-open (never raise)
# ═══════════════════════════════════════════════════════════════════════════════

def parse_tenant_modes(raw_json: str) -> dict[str, TenantMode]:
    """
    JSON string → tenant modes map.

    Fail-open: invalid JSON → empty dict + log.
    Invalid mode values → skip that tenant + log.
    Empty string → empty dict (not an error).
    """
    if not raw_json or not raw_json.strip():
        return {}

    try:
        parsed = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        logger.warning("[GUARD-DECISION] parse_tenant_modes: invalid JSON, returning empty map")
        return {}

    if not isinstance(parsed, dict):
        logger.warning("[GUARD-DECISION] parse_tenant_modes: expected JSON object, got %s", type(parsed).__name__)
        return {}

    result: dict[str, TenantMode] = {}
    for tenant_id, mode_val in parsed.items():
        try:
            result[str(tenant_id)] = TenantMode(mode_val)
        except (ValueError, KeyError):
            logger.warning(
                "[GUARD-DECISION] parse_tenant_modes: invalid mode %r for tenant %r, skipping",
                mode_val, tenant_id,
            )
    return result


def parse_tenant_allowlist(raw_json: str) -> frozenset[str]:
    """
    JSON string → tenant allowlist frozenset.

    Fail-open: invalid JSON → empty frozenset + log.
    Non-list JSON → empty frozenset + log.
    Empty string → empty frozenset (not an error).
    """
    if not raw_json or not raw_json.strip():
        return frozenset()

    try:
        parsed = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        logger.warning("[GUARD-DECISION] parse_tenant_allowlist: invalid JSON, returning empty set")
        return frozenset()

    if not isinstance(parsed, list):
        logger.warning("[GUARD-DECISION] parse_tenant_allowlist: expected JSON array, got %s", type(parsed).__name__)
        return frozenset()

    return frozenset(str(item) for item in parsed)


# ═══════════════════════════════════════════════════════════════════════════════
# Tenant ID / mode resolution — pure functions
# ═══════════════════════════════════════════════════════════════════════════════

def sanitize_tenant_id(raw: str | None) -> str:
    """
    Tenant ID normalization.

    None, empty string, or whitespace-only → "default".
    Otherwise → stripped value.

    Pure function: no side effects.
    """
    if raw is None:
        return "default"
    stripped = raw.strip()
    return stripped if stripped else "default"


def resolve_tenant_mode(
    tenant_id: str | None,
    default_mode: TenantMode,
    tenant_modes: dict[str, TenantMode],
) -> TenantMode:
    """
    Deterministic tenant mode resolution.

    1. Normalize tenant_id via sanitize_tenant_id
    2. Look up in tenant_modes map
    3. If not found → return default_mode

    Pure function: same input → same output, no side effects.
    """
    normalized = sanitize_tenant_id(tenant_id)
    return tenant_modes.get(normalized, default_mode)

def sanitize_metric_tenant(
    tenant_id: str,
    allowlist: frozenset[str],
) -> str:
    """
    Sanitize tenant ID for metric labels (cardinality control).

    If tenant_id is in allowlist → return tenant_id.
    Otherwise → return "_other".
    Empty allowlist → always "_other".

    Pure function: no side effects.
    """
    if allowlist and tenant_id in allowlist:
        return tenant_id
    return "_other"



# ═══════════════════════════════════════════════════════════════════════════════
# Data models — all frozen
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class WindowParams:
    """Risk evaluation window parameters. Included in hash."""
    max_config_age_ms: int = 86_400_000      # 24h default
    clock_skew_allowance_ms: int = 5_000     # 5s default


@dataclass(frozen=True)
class GuardSignal:
    """Single normalized guard/data-source signal."""
    name: SignalName
    status: SignalStatus
    reason_code: SignalReasonCode
    observed_at_ms: int
    detail: str = ""  # debug-only, never in Prom labels


@dataclass(frozen=True)
class GuardDecisionSnapshot:
    """
    Per-request immutable decision record.

    Frozen: no field can be modified after creation.
    Produced once at request start by SnapshotFactory.build().
    """
    now_ms: int
    tenant_id: str
    endpoint: str
    method: str
    window_params: WindowParams
    config_hash: str
    risk_context_hash: str
    guard_deny_reason: Optional[GuardDenyReason]
    signals: tuple[GuardSignal, ...]
    derived_has_stale: bool
    derived_has_insufficient: bool
    is_degrade_mode: bool
    tenant_mode: TenantMode = TenantMode.SHADOW


# ═══════════════════════════════════════════════════════════════════════════════
# derive_signal_flags — single source of truth for stale/insufficient
# ═══════════════════════════════════════════════════════════════════════════════

def derive_signal_flags(
    signals: tuple[GuardSignal, ...],
) -> tuple[bool, bool]:
    """
    Derive (has_stale, has_insufficient) from signals only.

    Caller flags (anyStale/anyInsufficient) are NOT accepted.
    This is the defensive guarantee: even if a caller mis-sets flags,
    the decision logic uses only what signals actually report.

    Returns:
        (has_stale, has_insufficient)
    """
    has_stale = any(s.status == SignalStatus.STALE for s in signals)
    has_insufficient = any(s.status == SignalStatus.INSUFFICIENT for s in signals)
    return has_stale, has_insufficient


# ═══════════════════════════════════════════════════════════════════════════════
# Signal producers
# ═══════════════════════════════════════════════════════════════════════════════

def check_config_freshness(
    config: GuardConfig,
    now_ms: int,
    window_params: WindowParams,
) -> GuardSignal:
    """
    Config freshness signal producer.

    Rules (R4):
      - last_updated_at empty string → INSUFFICIENT (CONFIG_TIMESTAMP_MISSING)
      - last_updated_at parse error → INSUFFICIENT (CONFIG_TIMESTAMP_PARSE_ERROR)
      - age > max_config_age_ms + clock_skew_allowance_ms → STALE
      - else → OK
    """
    raw = config.last_updated_at

    if not raw or not raw.strip():
        return GuardSignal(
            name=SignalName.CONFIG_FRESHNESS,
            status=SignalStatus.INSUFFICIENT,
            reason_code=SignalReasonCode.CONFIG_TIMESTAMP_MISSING,
            observed_at_ms=now_ms,
            detail="last_updated_at is empty",
        )

    try:
        # Try ISO 8601 parse
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        config_ts_ms = int(dt.timestamp() * 1000)
    except (ValueError, TypeError, OverflowError):
        return GuardSignal(
            name=SignalName.CONFIG_FRESHNESS,
            status=SignalStatus.INSUFFICIENT,
            reason_code=SignalReasonCode.CONFIG_TIMESTAMP_PARSE_ERROR,
            observed_at_ms=now_ms,
            detail=f"cannot parse: {raw!r}",
        )

    age_ms = now_ms - config_ts_ms
    threshold = window_params.max_config_age_ms + window_params.clock_skew_allowance_ms

    if age_ms > threshold:
        return GuardSignal(
            name=SignalName.CONFIG_FRESHNESS,
            status=SignalStatus.STALE,
            reason_code=SignalReasonCode.CONFIG_STALE,
            observed_at_ms=now_ms,
            detail=f"age_ms={age_ms} > threshold={threshold}",
        )

    return GuardSignal(
        name=SignalName.CONFIG_FRESHNESS,
        status=SignalStatus.OK,
        reason_code=SignalReasonCode.OK,
        observed_at_ms=now_ms,
    )


def check_cb_mapping(
    endpoint: str,
    dependencies: list | None,
    now_ms: int,
) -> GuardSignal:
    """
    CB mapping signal producer.

    Rules (R4):
      - dependencies is None or empty → INSUFFICIENT (CB_MAPPING_MISS)
      - else → OK
    """
    if not dependencies:
        return GuardSignal(
            name=SignalName.CB_MAPPING,
            status=SignalStatus.INSUFFICIENT,
            reason_code=SignalReasonCode.CB_MAPPING_MISS,
            observed_at_ms=now_ms,
            detail=f"no mapping for {endpoint}",
        )

    return GuardSignal(
        name=SignalName.CB_MAPPING,
        status=SignalStatus.OK,
        reason_code=SignalReasonCode.OK,
        observed_at_ms=now_ms,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Hash computation
# ═══════════════════════════════════════════════════════════════════════════════

def compute_risk_context_hash(
    tenant_id: str,
    endpoint: str,
    method: str,
    config_hash: str,
    window_params: WindowParams,
    guard_deny_reason_name: str | None,
    derived_has_stale: bool,
    derived_has_insufficient: bool,
) -> str:
    """
    Deterministic risk context hash. Includes windowParams (R5).

    Canonicalization: json.dumps(sort_keys=True, separators=(',',':'))
    Hash: SHA-256, first 16 hex chars.
    """
    payload = {
        "tenant_id": tenant_id,
        "endpoint": endpoint,
        "method": method,
        "config_hash": config_hash,
        "window_params": {
            "max_config_age_ms": window_params.max_config_age_ms,
            "clock_skew_allowance_ms": window_params.clock_skew_allowance_ms,
        },
        "guard_deny_reason": guard_deny_reason_name,
        "derived_has_stale": derived_has_stale,
        "derived_has_insufficient": derived_has_insufficient,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


# ═══════════════════════════════════════════════════════════════════════════════
# SnapshotFactory
# ═══════════════════════════════════════════════════════════════════════════════

class SnapshotFactory:
    """
    Produces per-request immutable GuardDecisionSnapshot.

    Fail-open: build() exception → None + log (R6).
    """

    @staticmethod
    def build(
        guard_deny_reason: GuardDenyReason | None,
        config: GuardConfig,
        endpoint: str,
        method: str,
        dependencies: list | None = None,
        tenant_id: str = "default",
        is_degrade_mode: bool = False,
        window_params: WindowParams | None = None,
        now_ms: int | None = None,
    ) -> GuardDecisionSnapshot | None:
        """
        Build immutable snapshot. All signals evaluated, flags derived,
        hash computed, result frozen.

        Returns None on internal error (fail-open).
        """
        try:
            if now_ms is None:
                now_ms = int(time.time() * 1000)
            if window_params is None:
                window_params = WindowParams()

            # Resolve tenant mode from config
            tenant_modes = parse_tenant_modes(config.decision_layer_tenant_modes_json)
            try:
                default_mode = TenantMode(config.decision_layer_default_mode)
            except (ValueError, KeyError):
                default_mode = TenantMode.SHADOW
            tenant_mode = resolve_tenant_mode(tenant_id, default_mode, tenant_modes)

            # Collect signals
            sig_config = check_config_freshness(config, now_ms, window_params)
            sig_mapping = check_cb_mapping(endpoint, dependencies, now_ms)
            signals = (sig_config, sig_mapping)

            # Derive flags from signals only
            has_stale, has_insufficient = derive_signal_flags(signals)

            # Compute hash (includes window_params)
            deny_name = guard_deny_reason.value if guard_deny_reason else None
            risk_hash = compute_risk_context_hash(
                tenant_id=tenant_id,
                endpoint=endpoint,
                method=method,
                config_hash=config.config_hash,
                window_params=window_params,
                guard_deny_reason_name=deny_name,
                derived_has_stale=has_stale,
                derived_has_insufficient=has_insufficient,
            )

            return GuardDecisionSnapshot(
                now_ms=now_ms,
                tenant_id=tenant_id,
                endpoint=endpoint,
                method=method,
                window_params=window_params,
                config_hash=config.config_hash,
                risk_context_hash=risk_hash,
                guard_deny_reason=guard_deny_reason,
                signals=signals,
                derived_has_stale=has_stale,
                derived_has_insufficient=has_insufficient,
                is_degrade_mode=is_degrade_mode,
                tenant_mode=tenant_mode,
            )

        except Exception as exc:
            logger.error(f"[GUARD-DECISION] SnapshotFactory.build() failed: {exc}")
            return None
