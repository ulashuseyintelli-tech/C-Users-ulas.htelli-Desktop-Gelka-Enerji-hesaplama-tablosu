"""
AlertValidator — deterministic PromQL alert evaluation for CI.

Parses alert YAML and evaluates simplified PromQL expressions
against metric snapshots. No real Prometheus server required.

Feature: fault-injection, Task 11.1
Requirements: 8.4
"""

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class AlertEvalResult:
    """Result of a simplified PromQL evaluation."""
    alert_name: str
    expr: str
    would_fire: bool
    metric_value: float | None
    threshold: float | None


class AlertValidator:
    """
    Evaluate alert PromQL expressions against metric snapshots.

    Deterministic, CI-safe — no Prometheus server needed.
    """

    def __init__(self, alerts_path: str | None = None):
        if alerts_path is None:
            # Auto-resolve: try relative to workspace root or backend/
            candidates = [
                Path("monitoring/prometheus/ptf-admin-alerts.yml"),
                Path("../monitoring/prometheus/ptf-admin-alerts.yml"),
            ]
            for candidate in candidates:
                if candidate.exists():
                    alerts_path = str(candidate)
                    break
            else:
                alerts_path = str(candidates[0])  # will raise FileNotFoundError
        self._alerts = self._load_alerts(alerts_path)

    def _load_alerts(self, path: str) -> dict[str, dict]:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        alerts: dict[str, dict] = {}
        for group in data["spec"]["groups"]:
            for rule in group["rules"]:
                if "alert" in rule:
                    alerts[rule["alert"]] = rule
        return alerts

    @property
    def alert_names(self) -> list[str]:
        return list(self._alerts.keys())

    def check_circuit_breaker_open(self, cb_states: dict[str, int]) -> AlertEvalResult:
        """
        PTFAdminCircuitBreakerOpen: max(ptf_admin_circuit_breaker_state) == 2

        Args:
            cb_states: {dependency_name: state_value} e.g. {"db_primary": 2}
        """
        alert = self._alerts["PTFAdminCircuitBreakerOpen"]
        max_state = max(cb_states.values()) if cb_states else 0
        return AlertEvalResult(
            alert_name="PTFAdminCircuitBreakerOpen",
            expr=alert["expr"],
            would_fire=max_state == 2,
            metric_value=float(max_state),
            threshold=2.0,
        )

    def check_rate_limit_spike(self, deny_rate_per_min: float) -> AlertEvalResult:
        """
        PTFAdminRateLimitSpike: deny rate > 5 req/min

        Args:
            deny_rate_per_min: rate of denied requests per minute
        """
        alert = self._alerts["PTFAdminRateLimitSpike"]
        return AlertEvalResult(
            alert_name="PTFAdminRateLimitSpike",
            expr=alert["expr"],
            would_fire=deny_rate_per_min > 5,
            metric_value=deny_rate_per_min,
            threshold=5.0,
        )

    def check_guard_internal_error(
        self, error_rate: float = 0.0, fallback_rate: float = 0.0
    ) -> AlertEvalResult:
        """
        PTFAdminGuardInternalError: error_rate > 0 or fallback_rate > 0

        Args:
            error_rate: rate of killswitch_error_total
            fallback_rate: rate of killswitch_fallback_open_total
        """
        alert = self._alerts["PTFAdminGuardInternalError"]
        return AlertEvalResult(
            alert_name="PTFAdminGuardInternalError",
            expr=alert["expr"],
            would_fire=error_rate > 0 or fallback_rate > 0,
            metric_value=max(error_rate, fallback_rate),
            threshold=0.0,
        )
