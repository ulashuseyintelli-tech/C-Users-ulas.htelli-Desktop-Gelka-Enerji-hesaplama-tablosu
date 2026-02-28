"""
Drift Guard — drift detection subsystem for Guard Decision Middleware.

Components:
    - DriftReasonCode: Closed-set enum (DRIFT: prefix)
    - DriftInput: Frozen dataclass — provider output
    - DriftBaseline: Frozen dataclass — startup baseline (v0)
    - DriftDecision: Frozen dataclass — evaluator output
    - DriftInputProvider: Protocol for input extraction
    - StubDriftInputProvider: No-drift baseline stub
    - HashDriftInputProvider: Deterministic hash-based provider (v0)
    - evaluate_drift: Pure function (v0: hash comparison)
    - build_baseline: Factory for startup baseline

Feature: drift-guard
Requirements: DR1.1, DR3.7, DR7.1-DR7.7
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, TYPE_CHECKING

from starlette.requests import Request

if TYPE_CHECKING:
    from ..guard_config import GuardConfig


# ═══════════════════════════════════════════════════════════════════════
# DriftReasonCode — closed set, DRIFT: prefix
# ═══════════════════════════════════════════════════════════════════════


class DriftReasonCode(str, Enum):
    """Closed-set drift reason codes. All values carry DRIFT: prefix."""
    PROVIDER_ERROR = "DRIFT:PROVIDER_ERROR"
    THRESHOLD_EXCEEDED = "DRIFT:THRESHOLD_EXCEEDED"
    INPUT_ANOMALY = "DRIFT:INPUT_ANOMALY"


# ═══════════════════════════════════════════════════════════════════════
# DriftInput — frozen provider output
# ═══════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class DriftInput:
    """Provider output for drift evaluation. Immutable."""
    endpoint: str
    method: str
    tenant_id: str
    request_signature: str
    config_hash: str = ""
    timestamp_ms: int = 0


# ═══════════════════════════════════════════════════════════════════════
# DriftDecision — frozen evaluator output
# ═══════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class DriftDecision:
    """Drift evaluation result. Immutable."""
    is_drift: bool
    reason_code: DriftReasonCode | None = None
    detail: str = ""
    would_enforce: bool = False


# ═══════════════════════════════════════════════════════════════════════
# DriftInputProvider protocol + stub + hash provider
# ═══════════════════════════════════════════════════════════════════════


class DriftInputProvider(Protocol):
    """Protocol for extracting drift input from a request."""
    def get_input(
        self, request: Request, endpoint: str, method: str, tenant_id: str,
    ) -> DriftInput: ...


class StubDriftInputProvider:
    """No-drift baseline: always returns a valid DriftInput."""
    def get_input(
        self, request: Request, endpoint: str, method: str, tenant_id: str,
    ) -> DriftInput:
        return DriftInput(
            endpoint=endpoint,
            method=method,
            tenant_id=tenant_id,
            request_signature="",
            config_hash="",
            timestamp_ms=int(time.time() * 1000),
        )


def _compute_endpoint_signature(endpoint: str, method: str, risk_class: str = "low") -> str:
    """Deterministic hash: endpoint + method + risk_class → sha256 hex."""
    raw = f"{endpoint}|{method}|{risk_class}"
    return hashlib.sha256(raw.encode()).hexdigest()


class HashDriftInputProvider:
    """v0 deterministic provider: config_hash + endpoint_signature hash. IO-free."""

    def get_input(
        self, request: Request, endpoint: str, method: str, tenant_id: str,
        *, config_hash: str = "", risk_class: str = "low",
    ) -> DriftInput:
        sig = _compute_endpoint_signature(endpoint, method, risk_class)
        return DriftInput(
            endpoint=endpoint,
            method=method,
            tenant_id=tenant_id,
            request_signature=sig,
            config_hash=config_hash,
            timestamp_ms=int(time.time() * 1000),
        )


# ═══════════════════════════════════════════════════════════════════════
# DriftBaseline — startup baseline (v0, in-memory)
# ═══════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class DriftBaseline:
    """Startup baseline. Immutable. Process lifetime boyunca sabit (v0)."""
    config_hash: str
    known_endpoint_signatures: frozenset[str]
    created_at_ms: int


def build_baseline(
    config_hash: str,
    known_endpoints: list[tuple[str, str, str]] | None = None,
) -> DriftBaseline:
    """
    Factory: build immutable baseline at startup.

    known_endpoints: list of (endpoint, method, risk_class) tuples.
    """
    sigs: set[str] = set()
    if known_endpoints:
        for ep, method, rc in known_endpoints:
            sigs.add(_compute_endpoint_signature(ep, method, rc))
    return DriftBaseline(
        config_hash=config_hash,
        known_endpoint_signatures=frozenset(sigs),
        created_at_ms=int(time.time() * 1000),
    )


# ═══════════════════════════════════════════════════════════════════════
# evaluate_drift — pure function (v0: hash comparison)
# ═══════════════════════════════════════════════════════════════════════


def evaluate_drift(
    drift_input: DriftInput,
    baseline: DriftBaseline | None = None,
) -> DriftDecision:
    """
    Pure function: DriftInput × DriftBaseline → DriftDecision.

    v0 policy:
      - baseline is None → no-drift (backward compat with stub)
      - config_hash mismatch → DRIFT:THRESHOLD_EXCEEDED
      - unknown endpoint signature → DRIFT:INPUT_ANOMALY
      - else → no drift
    """
    if baseline is None:
        return DriftDecision(is_drift=False)

    # Config hash mismatch → drift detected
    if drift_input.config_hash and drift_input.config_hash != baseline.config_hash:
        return DriftDecision(
            is_drift=True,
            reason_code=DriftReasonCode.THRESHOLD_EXCEEDED,
            detail=f"config_hash mismatch: input={drift_input.config_hash[:8]}.. baseline={baseline.config_hash[:8]}..",
        )

    # Unknown endpoint signature → drift detected
    if (
        drift_input.request_signature
        and baseline.known_endpoint_signatures
        and drift_input.request_signature not in baseline.known_endpoint_signatures
    ):
        return DriftDecision(
            is_drift=True,
            reason_code=DriftReasonCode.INPUT_ANOMALY,
            detail=f"unknown endpoint signature: {drift_input.endpoint} {drift_input.method}",
        )

    return DriftDecision(is_drift=False)
