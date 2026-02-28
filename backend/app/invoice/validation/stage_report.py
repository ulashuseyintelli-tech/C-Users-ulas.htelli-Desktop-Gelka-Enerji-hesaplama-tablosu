"""Aşama sonu telemetri raporu üretici (Faz H0).

Structured JSON rapor üretir. PII içermez.
Gate evaluator sonuçlarını ve metrik snapshot'ını birleştirir.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from .gate_evaluator import GateDecision

logger = logging.getLogger(__name__)

_REQUIRED_FIELDS = frozenset({
    "stage",
    "generated_at",
    "observation_days",
    "total_invoices",
    "latency",
    "mismatch",
    "enforcement",
    "gate_decision",
})


@dataclass(frozen=True)
class LatencySnapshot:
    """Phase bazlı P95/P99 değerleri (ms)."""

    total_p95_ms: float
    total_p99_ms: float
    shadow_p95_ms: float | None = None
    shadow_p99_ms: float | None = None
    enforcement_p95_ms: float | None = None
    enforcement_p99_ms: float | None = None


@dataclass(frozen=True)
class MismatchSnapshot:
    """Uyumsuzluk sayaçları."""

    actionable_count: int
    whitelisted_count: int


@dataclass(frozen=True)
class EnforcementSnapshot:
    """Enforcement sayaçları."""

    soft_warn_count: int = 0
    hard_block_count: int = 0
    unexpected_block_count: int = 0
    retry_loop_count: int = 0


@dataclass(frozen=True)
class MetricsSnapshot:
    """Aşama sonu metrik snapshot'ı."""

    latency: LatencySnapshot
    mismatch: MismatchSnapshot
    enforcement: EnforcementSnapshot


def generate_stage_report(
    stage: str,
    observation_days: int,
    total_invoices: int,
    metrics: MetricsSnapshot,
    gate_decision: GateDecision,
) -> dict[str, Any]:
    """Aşama sonu raporu üret. JSON-serializable dict döner.

    PII içermez. Fatura ID'leri dahil edilmez.
    """
    try:
        gate_results = []
        for r in gate_decision.results:
            gate_results.append({
                "gate": r.gate,
                "verdict": r.verdict.value,
                "reasons": list(r.reasons),
            })

        report: dict[str, Any] = {
            "stage": stage,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "observation_days": observation_days,
            "total_invoices": total_invoices,
            "latency": asdict(metrics.latency),
            "mismatch": asdict(metrics.mismatch),
            "enforcement": asdict(metrics.enforcement),
            "gate_decision": {
                "overall": gate_decision.overall.value,
                "gates": gate_results,
            },
        }
        return report
    except Exception:
        logger.warning("generate_stage_report: beklenmeyen hata", exc_info=True)
        return {
            "stage": stage,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "error": "report generation failed",
            "observation_days": 0,
            "total_invoices": 0,
            "latency": {},
            "mismatch": {},
            "enforcement": {},
            "gate_decision": {"overall": "error", "gates": []},
        }


def report_to_json(report: dict[str, Any]) -> str:
    """Raporu JSON string'e çevir."""
    return json.dumps(report, indent=2, ensure_ascii=False, default=str)


def validate_report_structure(report: dict[str, Any]) -> bool:
    """Rapor yapısının zorunlu alanları içerdiğini doğrula."""
    return _REQUIRED_FIELDS.issubset(report.keys())
