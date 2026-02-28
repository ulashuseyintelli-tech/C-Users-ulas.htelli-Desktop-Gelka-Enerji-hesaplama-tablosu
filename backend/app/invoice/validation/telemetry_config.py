"""Gecikme bütçesi ve mod yapılandırması (Faz G + H0 wiring).

Config keys:
  INVOICE_VALIDATION_LATENCY_BUDGET_P95_MS — opsiyonel, pozitif float
  INVOICE_VALIDATION_LATENCY_BUDGET_P99_MS — opsiyonel, pozitif float
  INVOICE_VALIDATION_MODE                  — enforcement_config'den okunur;
                                             geçersiz → default "shadow" + log

H0 wiring:
  rollout_stage resolver — RolloutConfig'den stage bilgisi sağlar.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from .rollout_config import RolloutConfig, load_rollout_config

logger = logging.getLogger(__name__)

# --- Geçerli mod değerleri (enforcement_config ile tutarlı) ---
_VALID_MODES: frozenset[str] = frozenset({"off", "shadow", "enforce_soft", "enforce_hard"})
_DEFAULT_MODE = "shadow"


@dataclass(frozen=True)
class LatencyBudgetConfig:
    """Immutable gecikme bütçesi config. None = tanımsız (yalnızca ölçüm)."""

    p95_ms: float | None = None
    p99_ms: float | None = None


def _parse_positive_float(raw: str, name: str) -> float | None:
    """Pozitif float parse et. Geçersiz → None + log (fail-closed, ValueError yok)."""
    stripped = raw.strip()
    if not stripped:
        return None
    try:
        val = float(stripped)
        if val <= 0:
            logger.warning("%s: pozitif olmalı, değer=%s — bütçe devre dışı", name, raw)
            return None
        return val
    except (ValueError, TypeError):
        logger.warning("%s: geçersiz değer=%r — bütçe devre dışı", name, raw)
        return None


def load_latency_budget_config() -> LatencyBudgetConfig:
    """Ortam değişkenlerinden gecikme bütçesi oku."""
    p95 = _parse_positive_float(
        os.environ.get("INVOICE_VALIDATION_LATENCY_BUDGET_P95_MS", ""),
        "INVOICE_VALIDATION_LATENCY_BUDGET_P95_MS",
    )
    p99 = _parse_positive_float(
        os.environ.get("INVOICE_VALIDATION_LATENCY_BUDGET_P99_MS", ""),
        "INVOICE_VALIDATION_LATENCY_BUDGET_P99_MS",
    )
    return LatencyBudgetConfig(p95_ms=p95, p99_ms=p99)


def resolve_mode(raw: str | None = None) -> str:
    """Mod string'ini çözümle. Geçersiz → default 'shadow' + log.

    raw=None ise ortam değişkeninden okur.
    """
    if raw is None:
        raw = os.environ.get("INVOICE_VALIDATION_MODE", "")
    cleaned = raw.strip().lower() if raw else ""
    if cleaned in _VALID_MODES:
        return cleaned
    if cleaned:
        logger.warning(
            "resolve_mode: geçersiz mode=%r — varsayılan '%s' kullanılıyor",
            raw,
            _DEFAULT_MODE,
        )
    return _DEFAULT_MODE


def resolve_rollout_stage() -> str | None:
    """Rollout stage'i RolloutConfig'den çözümle. None = tanımsız."""
    cfg = load_rollout_config()
    return cfg.rollout_stage
