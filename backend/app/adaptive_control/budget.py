"""
Error Budget Calculator — rolling 30d aggregator + reset semantics.

Formula: allowed_errors = (1 - SLO_target) × window_duration × request_rate
Rolling 30-day window (continuous, not calendar month).
Budget reset only via config change + audit log.

Feature: slo-adaptive-control, Task 5.1
Requirements: 3.1, 3.2, 3.3, 3.4, 3.6, 3.7
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from backend.app.testing.slo_evaluator import MetricSample

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ErrorBudgetConfig:
    """Configuration for a single error budget."""
    subsystem_id: str
    metric: str  # e.g. "5xx_rate", "failed_jobs_rate"
    window_seconds: int = 30 * 86400  # 30 days
    slo_target: float = 0.999
    burn_rate_threshold: float = 1.0


@dataclass(frozen=True)
class BudgetStatus:
    """Result of error budget evaluation for a subsystem."""
    subsystem_id: str
    metric: str
    budget_total: float
    budget_consumed: float
    budget_remaining_pct: float
    burn_rate: float
    is_exhausted: bool
    is_burn_rate_exceeded: bool


class ErrorBudgetCalculator:
    """
    Evaluates error budgets using rolling 30-day windows.

    Formula (Req 3.6):
        allowed_errors = (1 - SLO_target) × window_duration × request_rate

    Reset only via config change (Req 3.7).
    """

    def __init__(self, configs: Optional[list[ErrorBudgetConfig]] = None) -> None:
        self._configs = configs or []
        self._config_version: int = 0

    @property
    def configs(self) -> list[ErrorBudgetConfig]:
        return list(self._configs)

    def update_configs(
        self,
        new_configs: list[ErrorBudgetConfig],
        actor: str = "system",
    ) -> dict:
        """Update budget configs. This is the only way to reset budgets (Req 3.7)."""
        old_version = self._config_version
        self._config_version += 1
        self._configs = list(new_configs)
        audit = {
            "action": "budget_config_update",
            "old_version": old_version,
            "new_version": self._config_version,
            "actor": actor,
            "config_count": len(new_configs),
        }
        logger.info(f"[ADAPTIVE-CONTROL] Budget config updated (reset): {audit}")
        return audit

    def evaluate(
        self,
        samples: list[MetricSample],
        now_ms: int,
    ) -> list[BudgetStatus]:
        """Evaluate all configured error budgets (Req 3.1, 3.2)."""
        results: list[BudgetStatus] = []
        for cfg in self._configs:
            status = self._evaluate_single(cfg, samples, now_ms)
            results.append(status)
        return results

    def _evaluate_single(
        self,
        cfg: ErrorBudgetConfig,
        samples: list[MetricSample],
        now_ms: int,
    ) -> BudgetStatus:
        """Evaluate a single error budget."""
        window_start_ms = now_ms - (cfg.window_seconds * 1000)
        in_window = [
            s for s in samples
            if window_start_ms <= s.timestamp_ms <= now_ms
        ]

        if not in_window:
            return BudgetStatus(
                subsystem_id=cfg.subsystem_id,
                metric=cfg.metric,
                budget_total=0.0,
                budget_consumed=0.0,
                budget_remaining_pct=100.0,
                burn_rate=0.0,
                is_exhausted=False,
                is_burn_rate_exceeded=False,
            )

        # Calculate request rate (requests per second over window)
        total_requests = sum(s.total_requests for s in in_window)
        window_duration_s = cfg.window_seconds
        request_rate = total_requests / window_duration_s if window_duration_s > 0 else 0.0

        # Budget formula (Req 3.6):
        # allowed_errors = (1 - SLO_target) × window_duration × request_rate
        error_fraction = 1.0 - cfg.slo_target
        budget_total = error_fraction * window_duration_s * request_rate

        # Actual errors
        total_errors = sum(
            s.total_requests - s.successful_requests for s in in_window
        )
        budget_consumed = float(total_errors)

        # Remaining percentage
        if budget_total > 0:
            budget_remaining_pct = max(0.0, (1.0 - budget_consumed / budget_total) * 100.0)
        else:
            # Zero budget (SLO target = 1.0 or zero requests)
            budget_remaining_pct = 0.0 if budget_consumed > 0 else 100.0

        # Burn rate: actual error rate / allowed error rate
        # burn_rate > 1.0 means consuming budget faster than allowed
        if budget_total > 0:
            burn_rate = budget_consumed / budget_total
        else:
            burn_rate = float("inf") if budget_consumed > 0 else 0.0

        is_exhausted = budget_remaining_pct <= 0.0
        is_burn_rate_exceeded = burn_rate > cfg.burn_rate_threshold

        return BudgetStatus(
            subsystem_id=cfg.subsystem_id,
            metric=cfg.metric,
            budget_total=budget_total,
            budget_consumed=budget_consumed,
            budget_remaining_pct=budget_remaining_pct,
            burn_rate=burn_rate,
            is_exhausted=is_exhausted,
            is_burn_rate_exceeded=is_burn_rate_exceeded,
        )
