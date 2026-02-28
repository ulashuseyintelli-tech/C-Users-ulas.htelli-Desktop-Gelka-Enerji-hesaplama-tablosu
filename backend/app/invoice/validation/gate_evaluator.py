"""Stateless baraj değerlendirme modülü (Faz H0).

Tüm fonksiyonlar pure function'dır — state yönetimi upstream'in sorumluluğundadır.
Gate modülü veri kaynağını (Prometheus, in-memory, dosya) bilmez.
Exception atmaz; fail-closed (log + GateResult).

Fail semantiği:
  - mismatch, safety, unexpected_block: FAIL (fail-closed)
  - latency enabled + internal error: DEFER (ölçemiyorsan geçme)
  - latency disabled (delta_ms=None): PASS
  - mismatch disabled (threshold=None): PASS
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class GateVerdict(str, Enum):
    """Baraj karar sonucu."""

    PASS = "pass"
    FAIL = "fail"
    DEFER = "defer"


@dataclass(frozen=True)
class GateResult:
    """Tek bir baraj değerlendirmesinin sonucu."""

    gate: str
    verdict: GateVerdict
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class GateDecision:
    """Tüm barajların birleşik sonucu."""

    results: tuple[GateResult, ...] = ()

    @property
    def overall(self) -> GateVerdict:
        """Birleşik karar: FAIL > DEFER > PASS."""
        verdicts = {r.verdict for r in self.results}
        if GateVerdict.FAIL in verdicts:
            return GateVerdict.FAIL
        if GateVerdict.DEFER in verdicts:
            return GateVerdict.DEFER
        return GateVerdict.PASS


# ---------------------------------------------------------------------------
# N_min kontrolü
# ---------------------------------------------------------------------------

def check_n_min(observed_count: int, n_min: int) -> GateResult:
    """Minimum örneklem kontrolü. < N_min → DEFER."""
    try:
        if observed_count < n_min:
            return GateResult(
                gate="n_min",
                verdict=GateVerdict.DEFER,
                reasons=(f"observed={observed_count} < n_min={n_min}",),
            )
        return GateResult(gate="n_min", verdict=GateVerdict.PASS)
    except Exception:
        logger.warning("check_n_min: beklenmeyen hata — DEFER", exc_info=True)
        return GateResult(gate="n_min", verdict=GateVerdict.DEFER, reasons=("internal error",))


# ---------------------------------------------------------------------------
# Gecikme barajı
# ---------------------------------------------------------------------------

def evaluate_latency_gate(
    baseline_p95: float,
    baseline_p99: float,
    current_p95: float,
    current_p99: float,
    delta_ms: float | None,
) -> GateResult:
    """Gecikme barajı: current ≤ baseline + Δ.

    delta_ms=None → PASS (gate disabled).
    delta_ms not None + internal error → DEFER (ölçemiyorsan geçme).
    """
    if delta_ms is None:
        return GateResult(
            gate="latency",
            verdict=GateVerdict.PASS,
            reasons=("delta_ms tanımsız — baraj devre dışı",),
        )
    try:
        reasons: list[str] = []
        failed = False

        if current_p95 > baseline_p95 + delta_ms:
            reasons.append(
                f"P95 ihlal: {current_p95:.2f} > {baseline_p95:.2f} + {delta_ms:.2f}"
            )
            failed = True

        if current_p99 > baseline_p99 + delta_ms:
            reasons.append(
                f"P99 ihlal: {current_p99:.2f} > {baseline_p99:.2f} + {delta_ms:.2f}"
            )
            failed = True

        verdict = GateVerdict.FAIL if failed else GateVerdict.PASS
        if failed:
            logger.warning("latency gate FAIL: %s", "; ".join(reasons))
        return GateResult(gate="latency", verdict=verdict, reasons=tuple(reasons))
    except Exception:
        logger.warning("evaluate_latency_gate: beklenmeyen hata — DEFER", exc_info=True)
        return GateResult(gate="latency", verdict=GateVerdict.DEFER, reasons=("internal error — ölçüm yapılamadı",))


# ---------------------------------------------------------------------------
# Uyumsuzluk barajı
# ---------------------------------------------------------------------------

def evaluate_mismatch_gate(
    actionable_count: int,
    threshold: int | None = 0,
) -> GateResult:
    """Uyumsuzluk barajı: actionable_count ≤ threshold.

    threshold=None → PASS (gate disabled).
    threshold=0 (varsayılan) → tek actionable bile FAIL.
    """
    if threshold is None:
        return GateResult(
            gate="mismatch",
            verdict=GateVerdict.PASS,
            reasons=("threshold=None — baraj devre dışı",),
        )
    try:
        if actionable_count > threshold:
            reason = f"actionable={actionable_count} > threshold={threshold}"
            logger.warning("mismatch gate FAIL: %s", reason)
            return GateResult(gate="mismatch", verdict=GateVerdict.FAIL, reasons=(reason,))
        return GateResult(gate="mismatch", verdict=GateVerdict.PASS)
    except Exception:
        logger.warning("evaluate_mismatch_gate: beklenmeyen hata — FAIL (fail-closed)", exc_info=True)
        return GateResult(gate="mismatch", verdict=GateVerdict.FAIL, reasons=("internal error — fail-closed",))


# ---------------------------------------------------------------------------
# Güvenlik barajı (Safety) — retry loop
# ---------------------------------------------------------------------------

def evaluate_safety_gate(retry_loop_count: int) -> GateResult:
    """Safety barajı: retry_loop_count == 0. Kesin — ihlali → anında rollback."""
    try:
        if retry_loop_count != 0:
            reason = f"retry_loop_count={retry_loop_count} != 0"
            logger.warning("safety gate FAIL: %s", reason)
            return GateResult(gate="safety", verdict=GateVerdict.FAIL, reasons=(reason,))
        return GateResult(gate="safety", verdict=GateVerdict.PASS)
    except Exception:
        logger.warning("evaluate_safety_gate: beklenmeyen hata — FAIL (fail-closed)", exc_info=True)
        return GateResult(gate="safety", verdict=GateVerdict.FAIL, reasons=("internal error — fail-closed",))


# ---------------------------------------------------------------------------
# Unexpected block barajı
# ---------------------------------------------------------------------------

def evaluate_unexpected_block_gate(
    unexpected_block_count: int,
    threshold: int = 0,
) -> GateResult:
    """Unexpected block barajı: unexpected_block_count ≤ threshold.

    Unexpected block = bilinen doğrulama kuralına veya dokümante edilmiş iş
    senaryosuna eşlenemeyen blok. Varsayılan threshold=0 → tek unexpected bile FAIL.
    D2'de herhangi bir unexpected block → anında rollback.
    """
    try:
        if unexpected_block_count > threshold:
            reason = f"unexpected_block_count={unexpected_block_count} > threshold={threshold}"
            logger.warning("unexpected_block gate FAIL: %s", reason)
            return GateResult(gate="unexpected_block", verdict=GateVerdict.FAIL, reasons=(reason,))
        return GateResult(gate="unexpected_block", verdict=GateVerdict.PASS)
    except Exception:
        logger.warning("evaluate_unexpected_block_gate: beklenmeyen hata — FAIL (fail-closed)", exc_info=True)
        return GateResult(gate="unexpected_block", verdict=GateVerdict.FAIL, reasons=("internal error — fail-closed",))


# ---------------------------------------------------------------------------
# Birleşik değerlendirme
# ---------------------------------------------------------------------------

_DEFERRED_REASON = "waiting_for_min_sample"


def evaluate_all_gates(
    *,
    observed_count: int,
    n_min: int,
    baseline_p95: float,
    baseline_p99: float,
    current_p95: float,
    current_p99: float,
    delta_ms: float | None,
    actionable_mismatch_count: int,
    mismatch_threshold: int | None = 0,
    retry_loop_count: int,
    unexpected_block_count: int = 0,
    unexpected_block_threshold: int = 0,
) -> GateDecision:
    """Tüm barajları değerlendir ve birleşik karar döndür.

    N_min sağlanmadıysa diğer barajlar da DEFER olarak dahil edilir
    (tek format — raporda "neden yok?" sorusu çıkmaz).
    """
    n_min_result = check_n_min(observed_count, n_min)

    if n_min_result.verdict == GateVerdict.DEFER:
        return GateDecision(results=(
            n_min_result,
            GateResult(gate="latency", verdict=GateVerdict.DEFER, reasons=(_DEFERRED_REASON,)),
            GateResult(gate="mismatch", verdict=GateVerdict.DEFER, reasons=(_DEFERRED_REASON,)),
            GateResult(gate="safety", verdict=GateVerdict.DEFER, reasons=(_DEFERRED_REASON,)),
            GateResult(gate="unexpected_block", verdict=GateVerdict.DEFER, reasons=(_DEFERRED_REASON,)),
        ))

    latency_result = evaluate_latency_gate(
        baseline_p95, baseline_p99, current_p95, current_p99, delta_ms
    )
    mismatch_result = evaluate_mismatch_gate(actionable_mismatch_count, mismatch_threshold)
    safety_result = evaluate_safety_gate(retry_loop_count)
    unexpected_result = evaluate_unexpected_block_gate(unexpected_block_count, unexpected_block_threshold)

    return GateDecision(
        results=(n_min_result, latency_result, mismatch_result, safety_result, unexpected_result)
    )
