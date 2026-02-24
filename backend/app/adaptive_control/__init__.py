"""
SLO-Aware Adaptive Control — Package init + factory function.

Feature: slo-adaptive-control, Task 11.1
Requirements: 4.1, 7.1, 8.1
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from backend.app.adaptive_control.budget import ErrorBudgetCalculator
from backend.app.adaptive_control.config import (
    AdaptiveControlConfig,
    AllowlistManager,
    load_adaptive_control_config,
)
from backend.app.adaptive_control.controller import AdaptiveController
from backend.app.adaptive_control.decision_engine import DecisionEngine
from backend.app.adaptive_control.hysteresis import HysteresisFilter
from backend.app.adaptive_control.metrics_collector import MetricsCollector
from backend.app.adaptive_control.sufficiency import (
    SufficiencyConfig,
    TelemetrySufficiencyChecker,
)

logger = logging.getLogger(__name__)


def create_adaptive_controller(
    config: Optional[AdaptiveControlConfig] = None,
    guard_mode_setter: Optional[Callable[[str], None]] = None,
    pdf_backpressure_setter: Optional[Callable[[bool], None]] = None,
    killswitch_active_fn: Optional[Callable[[str], bool]] = None,
    manual_override_active_fn: Optional[Callable[[str], bool]] = None,
) -> AdaptiveController:
    """Factory: create a fully-wired AdaptiveController.

    All components are instantiated and connected. Callers provide
    subsystem callbacks for side-effect application.

    Args:
        config: AdaptiveControlConfig (loads from env if None)
        guard_mode_setter: callback to set guard mode ("shadow"/"enforce")
        pdf_backpressure_setter: callback to set PDF backpressure (True/False)
        killswitch_active_fn: callback to check if killswitch is active for subsystem
        manual_override_active_fn: callback to check if manual override is active

    Returns:
        Fully wired AdaptiveController ready for tick() calls.
    """
    if config is None:
        config = load_adaptive_control_config()

    allowlist = AllowlistManager(config.targets)

    metrics_collector = MetricsCollector(
        stale_threshold_ms=int(config.control_loop_interval_seconds * 2 * 1000),
    )

    budget_calculator = ErrorBudgetCalculator()

    decision_engine = DecisionEngine(
        config=config,
        allowlist=allowlist,
        killswitch_active_fn=killswitch_active_fn,
        manual_override_active_fn=manual_override_active_fn,
    )

    hysteresis_filter = HysteresisFilter(
        dwell_time_ms=int(config.dwell_time_seconds * 1000),
        cooldown_ms=int(config.cooldown_period_seconds * 1000),
        oscillation_window_size=config.oscillation_window_size,
        oscillation_max_transitions=config.oscillation_max_transitions,
    )

    sufficiency_checker = TelemetrySufficiencyChecker(
        SufficiencyConfig(
            min_samples=max(1, int(config.min_sample_ratio)),
            min_bucket_coverage_pct=config.min_bucket_coverage_pct,
            check_source_stale=True,
        )
    )

    controller = AdaptiveController(
        config=config,
        metrics_collector=metrics_collector,
        budget_calculator=budget_calculator,
        decision_engine=decision_engine,
        hysteresis_filter=hysteresis_filter,
        sufficiency_checker=sufficiency_checker,
        guard_mode_setter=guard_mode_setter,
        pdf_backpressure_setter=pdf_backpressure_setter,
    )

    logger.info(
        "[ADAPTIVE-CONTROL] Controller created: "
        f"loop_interval={config.control_loop_interval_seconds}s, "
        f"targets={len(config.targets)}"
    )

    return controller
