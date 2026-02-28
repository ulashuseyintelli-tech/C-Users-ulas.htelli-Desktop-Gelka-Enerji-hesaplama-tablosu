"""Performans telemetrisi (Faz G).

Tek histogram + mod gauge. Kapalı küme phase label.
Timer context manager try/finally ile çalışır; exception'lar
telemetry'de asla propagate edilmez.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from enum import Enum
from typing import Generator

logger = logging.getLogger(__name__)


# --- Kapalı küme faz etiketi ---
class Phase(str, Enum):
    TOTAL = "total"
    SHADOW = "shadow"
    ENFORCEMENT = "enforcement"


VALID_PHASES: frozenset[str] = frozenset(p.value for p in Phase)

# --- Kapalı küme mod etiketi ---
VALID_MODES: frozenset[str] = frozenset({"off", "shadow", "enforce_soft", "enforce_hard"})


# --- Histogram (in-memory, test ortamı) ---
_duration_observations: dict[str, list[float]] = {
    "total": [],
    "shadow": [],
    "enforcement": [],
}


def observe_duration(phase: str, duration_seconds: float) -> None:
    """Histogram'a gözlem ekle. Geçersiz phase → log + skip (fail-closed)."""
    if phase not in VALID_PHASES:
        logger.error("observe_duration: geçersiz phase=%r, gözlem atlandı", phase)
        return
    _duration_observations[phase].append(duration_seconds)


def get_duration_observations() -> dict[str, list[float]]:
    """Test inspection: tüm gözlemlerin kopyası."""
    return {k: list(v) for k, v in _duration_observations.items()}


def reset_duration_observations() -> None:
    """Test cleanup."""
    for v in _duration_observations.values():
        v.clear()


# --- Mod Gauge ---
_mode_gauge: dict[str, int] = {
    "off": 0,
    "shadow": 0,
    "enforce_soft": 0,
    "enforce_hard": 0,
}


def set_mode_gauge(active_mode: str) -> None:
    """Aktif modu 1, diğerlerini 0 yap. Geçersiz mod → log + skip."""
    if active_mode not in VALID_MODES:
        logger.error("set_mode_gauge: geçersiz mode=%r, güncelleme atlandı", active_mode)
        return
    for m in _mode_gauge:
        _mode_gauge[m] = 1 if m == active_mode else 0


def get_mode_gauge() -> dict[str, int]:
    """Test inspection."""
    return dict(_mode_gauge)


def reset_mode_gauge() -> None:
    """Test cleanup."""
    for m in _mode_gauge:
        _mode_gauge[m] = 0


# --- Timer context manager ---
class Timer:
    """Basit zamanlayıcı — with bloğu ile kullanılır.

    try/finally garantisi: exception olsa bile elapsed hesaplanır,
    ancak exception asla yutulmaz — pipeline'a propagate olur.
    Timer kendisi asla exception fırlatmaz.
    """

    def __init__(self) -> None:
        self.start: float = 0.0
        self.elapsed: float = 0.0

    def __enter__(self) -> Timer:
        self.start = time.monotonic()
        return self

    def __exit__(self, *args: object) -> None:
        try:
            self.elapsed = time.monotonic() - self.start
        except Exception:
            logger.error("Timer.__exit__: süre hesaplama hatası", exc_info=True)
            self.elapsed = 0.0
