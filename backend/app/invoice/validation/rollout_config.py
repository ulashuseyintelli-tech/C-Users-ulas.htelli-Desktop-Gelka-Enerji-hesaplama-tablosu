"""Rollout konfigürasyonu (Faz H0).

Config keys:
  INVOICE_VALIDATION_LATENCY_GATE_DELTA_MS  — opsiyonel, pozitif float (ms)
  INVOICE_VALIDATION_MISMATCH_GATE_COUNT    — opsiyonel, non-negative int (varsayılan=0)
  INVOICE_VALIDATION_GATE_N_MIN             — opsiyonel, pozitif int (varsayılan=20)
  INVOICE_VALIDATION_ROLLOUT_STAGE          — opsiyonel, D0/D1/D2 (bilgi amaçlı)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_VALID_STAGES: frozenset[str] = frozenset({"D0", "D1", "D2"})
_DEFAULT_N_MIN = 20
_DEFAULT_MISMATCH_GATE_COUNT = 0


@dataclass(frozen=True)
class RolloutConfig:
    """Immutable rollout konfigürasyonu."""

    latency_gate_delta_ms: float | None = None
    mismatch_gate_count: int = _DEFAULT_MISMATCH_GATE_COUNT
    n_min: int = _DEFAULT_N_MIN
    rollout_stage: str | None = None


def _parse_positive_float(raw: str, name: str) -> float | None:
    """Pozitif float parse et. Geçersiz → None + log."""
    stripped = raw.strip()
    if not stripped:
        return None
    try:
        val = float(stripped)
        if val <= 0:
            logger.warning("%s: pozitif olmalı, değer=%s — devre dışı", name, raw)
            return None
        return val
    except (ValueError, TypeError):
        logger.warning("%s: geçersiz değer=%r — devre dışı", name, raw)
        return None


def _parse_non_negative_int(raw: str, name: str, default: int) -> int:
    """Non-negative int parse et. Geçersiz → default + log."""
    stripped = raw.strip()
    if not stripped:
        return default
    try:
        val = int(stripped)
        if val < 0:
            logger.warning("%s: negatif olamaz, değer=%s — varsayılan %d", name, raw, default)
            return default
        return val
    except (ValueError, TypeError):
        logger.warning("%s: geçersiz değer=%r — varsayılan %d", name, raw, default)
        return default


def _parse_positive_int(raw: str, name: str, default: int) -> int:
    """Pozitif int parse et. Geçersiz → default + log."""
    stripped = raw.strip()
    if not stripped:
        return default
    try:
        val = int(stripped)
        if val <= 0:
            logger.warning("%s: pozitif olmalı, değer=%s — varsayılan %d", name, raw, default)
            return default
        return val
    except (ValueError, TypeError):
        logger.warning("%s: geçersiz değer=%r — varsayılan %d", name, raw, default)
        return default


def _parse_stage(raw: str, name: str) -> str | None:
    """Rollout stage parse et. Geçersiz → None + log."""
    stripped = raw.strip().upper()
    if not stripped:
        return None
    if stripped in _VALID_STAGES:
        return stripped
    logger.warning("%s: geçersiz stage=%r — yok sayılıyor", name, raw)
    return None


def load_rollout_config() -> RolloutConfig:
    """Ortam değişkenlerinden rollout konfigürasyonu oku."""
    return RolloutConfig(
        latency_gate_delta_ms=_parse_positive_float(
            os.environ.get("INVOICE_VALIDATION_LATENCY_GATE_DELTA_MS", ""),
            "INVOICE_VALIDATION_LATENCY_GATE_DELTA_MS",
        ),
        mismatch_gate_count=_parse_non_negative_int(
            os.environ.get("INVOICE_VALIDATION_MISMATCH_GATE_COUNT", ""),
            "INVOICE_VALIDATION_MISMATCH_GATE_COUNT",
            _DEFAULT_MISMATCH_GATE_COUNT,
        ),
        n_min=_parse_positive_int(
            os.environ.get("INVOICE_VALIDATION_GATE_N_MIN", ""),
            "INVOICE_VALIDATION_GATE_N_MIN",
            _DEFAULT_N_MIN,
        ),
        rollout_stage=_parse_stage(
            os.environ.get("INVOICE_VALIDATION_ROLLOUT_STAGE", ""),
            "INVOICE_VALIDATION_ROLLOUT_STAGE",
        ),
    )
