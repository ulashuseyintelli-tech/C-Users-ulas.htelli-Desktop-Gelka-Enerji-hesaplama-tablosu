"""
PTF Admin Metrics — Prometheus-compatible observability.

Migrated from in-memory counters to prometheus_client types.
All metrics use the `ptf_admin_` namespace prefix.

Tracks:
- ptf_admin_upsert_total{status}: Upsert operations
- ptf_admin_import_rows_total{outcome}: Import row outcomes
- ptf_admin_import_apply_duration_seconds: Import apply duration
- ptf_admin_lookup_total{hit,status}: Lookup outcomes
- ptf_admin_history_query_total: History query count
- ptf_admin_history_query_duration_seconds: History query duration
- ptf_admin_api_request_total{endpoint,method,status_class}: HTTP request count
- ptf_admin_api_request_duration_seconds{endpoint}: HTTP request duration
- ptf_admin_frontend_events_total{event_name}: Frontend telemetry events

Feature: telemetry-unification, Task 1.1
"""

import logging
import time
from contextlib import contextmanager
from typing import Dict, Generator, Optional

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

logger = logging.getLogger(__name__)


class PTFMetrics:
    """
    Prometheus-compatible metrics for PTF Admin operations.

    Thread-safe via prometheus_client built-in thread safety.
    Uses instance-level CollectorRegistry for test isolation.

    snapshot() and reset() are intended for test/debug only —
    they SHALL NOT be used in production request paths.
    """

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self._registry = registry or CollectorRegistry()
        self._init_metrics()

    def _init_metrics(self) -> None:
        """Register all prometheus metrics on the current registry."""
        # ── Existing metrics (migrated) ───────────────────────────────────
        self._upsert_total = Counter(
            "ptf_admin_upsert_total",
            "Upsert operations",
            labelnames=["status"],
            registry=self._registry,
        )
        self._import_rows_total = Counter(
            "ptf_admin_import_rows_total",
            "Import row outcomes",
            labelnames=["outcome"],
            registry=self._registry,
        )
        self._import_apply_duration = Histogram(
            "ptf_admin_import_apply_duration_seconds",
            "Import apply operation duration",
            registry=self._registry,
        )
        self._lookup_total = Counter(
            "ptf_admin_lookup_total",
            "Lookup operations",
            labelnames=["hit", "status"],
            registry=self._registry,
        )

        # ── New metrics (Requirement 3) ───────────────────────────────────
        self._history_query_total = Counter(
            "ptf_admin_history_query_total",
            "History query operations",
            registry=self._registry,
        )
        self._history_query_duration = Histogram(
            "ptf_admin_history_query_duration_seconds",
            "History query duration",
            registry=self._registry,
        )
        self._api_request_total = Counter(
            "ptf_admin_api_request_total",
            "HTTP request count",
            labelnames=["endpoint", "method", "status_class"],
            registry=self._registry,
        )
        self._api_request_duration = Histogram(
            "ptf_admin_api_request_duration_seconds",
            "HTTP request duration",
            labelnames=["endpoint"],
            registry=self._registry,
        )

        # ── Frontend event counter (Requirement 6) ────────────────────────
        self._frontend_events_total = Counter(
            "ptf_admin_frontend_events_total",
            "Frontend telemetry events",
            labelnames=["event_name"],
            registry=self._registry,
        )

        # ── Ops-Guard metrics (Feature: ops-guard, Task 1.2) ─────────────
        self._guard_config_fallback_total = Counter(
            "ptf_admin_guard_config_fallback_total",
            "Guard config validation fallback count",
            registry=self._registry,
        )
        self._guard_config_schema_mismatch_total = Counter(
            "ptf_admin_guard_config_schema_mismatch_total",
            "Guard config schema mismatch count",
            registry=self._registry,
        )
        self._guard_config_loaded = Gauge(
            "ptf_admin_guard_config_loaded",
            "Active guard config (1=loaded)",
            labelnames=["schema_version", "config_version"],
            registry=self._registry,
        )
        self._slo_violation_total = Counter(
            "ptf_admin_slo_violation_total",
            "SLO violation events",
            labelnames=["slo_name"],
            registry=self._registry,
        )
        self._sentinel_impossible_state_total = Counter(
            "ptf_admin_sentinel_impossible_state_total",
            "Impossible state sentinel counter",
            registry=self._registry,
        )
        self._killswitch_state = Gauge(
            "ptf_admin_killswitch_state",
            "Kill-switch state (1=active, 0=passive)",
            labelnames=["switch_name"],
            registry=self._registry,
        )
        self._killswitch_error_total = Counter(
            "ptf_admin_killswitch_error_total",
            "Kill-switch internal error count",
            labelnames=["endpoint_class", "error_type"],
            registry=self._registry,
        )
        self._killswitch_fallback_open_total = Counter(
            "ptf_admin_killswitch_fallback_open_total",
            "Kill-switch fail-open fallback count",
            registry=self._registry,
        )
        self._rate_limit_total = Counter(
            "ptf_admin_rate_limit_total",
            "Rate limit decisions",
            labelnames=["endpoint", "decision"],
            registry=self._registry,
        )
        self._circuit_breaker_state = Gauge(
            "ptf_admin_circuit_breaker_state",
            "Circuit breaker state (0=closed, 1=half-open, 2=open)",
            labelnames=["dependency"],
            registry=self._registry,
        )

        # ── Dependency Wrapper metrics (Feature: dependency-wrappers, Task 2) ─
        self._dependency_call_total = Counter(
            "ptf_admin_dependency_call_total",
            "Dependency call outcomes",
            labelnames=["dependency", "outcome"],
            registry=self._registry,
        )
        self._dependency_call_duration = Histogram(
            "ptf_admin_dependency_call_duration_seconds",
            "Dependency call duration",
            labelnames=["dependency"],
            registry=self._registry,
        )
        self._dependency_retry_total = Counter(
            "ptf_admin_dependency_retry_total",
            "Dependency retry count",
            labelnames=["dependency"],
            registry=self._registry,
        )
        self._guard_failopen_total = Counter(
            "ptf_admin_guard_failopen_total",
            "Guard fail-open fallback count (middleware + wrapper)",
            registry=self._registry,
        )
        self._dependency_map_miss_total = Counter(
            "ptf_admin_dependency_map_miss_total",
            "Endpoint not found in dependency map (CB pre-check skipped)",
            registry=self._registry,
        )

    # ── upsert_total ──────────────────────────────────────────────────────

    def inc_upsert(self, status: str) -> None:
        """Increment upsert_total counter. status: 'provisional' | 'final'."""
        if status not in ("provisional", "final"):
            logger.warning(f"[METRICS] Invalid upsert status: {status}")
            return
        self._upsert_total.labels(status=status).inc()
        logger.debug(f"[METRICS] upsert_total{{status={status}}} += 1")

    # ── import_rows_total ─────────────────────────────────────────────────

    def inc_import_rows(self, outcome: str, count: int = 1) -> None:
        """Increment import_rows_total counter. outcome: 'accepted' | 'rejected'."""
        if outcome not in ("accepted", "rejected"):
            logger.warning(f"[METRICS] Invalid import_rows outcome: {outcome}")
            return
        self._import_rows_total.labels(outcome=outcome).inc(count)
        logger.debug(
            f"[METRICS] import_rows_total{{outcome={outcome}}} += {count}"
        )

    # ── import_apply_duration_seconds ─────────────────────────────────────

    def observe_import_apply_duration(self, duration_seconds: float) -> None:
        """Record the duration of an import/apply operation."""
        self._import_apply_duration.observe(duration_seconds)
        logger.debug(
            f"[METRICS] import_apply_duration_seconds={duration_seconds:.4f}"
        )

    @contextmanager
    def time_import_apply(self) -> Generator[None, None, None]:
        """Context manager to time an import/apply operation."""
        start = time.monotonic()
        try:
            yield
        finally:
            duration = time.monotonic() - start
            self.observe_import_apply_duration(duration)

    # ── lookup_total ──────────────────────────────────────────────────────

    def inc_lookup(self, hit: bool, status: Optional[str] = None) -> None:
        """Increment lookup_total counter."""
        if hit:
            if status not in ("provisional", "final"):
                logger.warning(
                    f"[METRICS] lookup hit=true requires valid status, got: {status}"
                )
                return
            self._lookup_total.labels(hit="true", status=status).inc()
        else:
            self._lookup_total.labels(hit="false", status="").inc()
        hit_str = "true" if hit else "false"
        logger.debug(f"[METRICS] lookup_total{{hit={hit_str}}} += 1")

    # ── history_query (new) ───────────────────────────────────────────────

    def inc_history_query(self) -> None:
        """Increment history_query_total counter."""
        self._history_query_total.inc()

    def observe_history_query_duration(self, duration_seconds: float) -> None:
        """Record the duration of a history query."""
        self._history_query_duration.observe(duration_seconds)

    @contextmanager
    def time_history_query(self) -> Generator[None, None, None]:
        """Context manager to time a history query."""
        start = time.monotonic()
        try:
            yield
        finally:
            duration = time.monotonic() - start
            self.observe_history_query_duration(duration)

    # ── api_request (new — used by middleware) ────────────────────────────

    def inc_api_request(self, endpoint: str, method: str, status_code: int) -> None:
        """Increment api_request_total counter.

        status_code is normalized to status_class (2xx/3xx/4xx/5xx/0xx)
        to prevent high-cardinality label explosion from exact HTTP codes.
        """
        status_class = f"{status_code // 100}xx"
        self._api_request_total.labels(
            endpoint=endpoint, method=method, status_class=status_class
        ).inc()

    def observe_api_request_duration(self, endpoint: str, duration: float) -> None:
        """Record HTTP request duration."""
        self._api_request_duration.labels(endpoint=endpoint).observe(duration)

    # ── frontend_events (new — used by event ingestion) ───────────────────

    def inc_frontend_event(self, event_name: str) -> None:
        """Increment frontend_events_total counter."""
        self._frontend_events_total.labels(event_name=event_name).inc()

    # ── Ops-Guard metrics (Feature: ops-guard, Task 1.2) ─────────────────

    def inc_guard_config_fallback(self) -> None:
        """Increment guard config fallback counter (HD-4)."""
        self._guard_config_fallback_total.inc()

    def inc_guard_config_schema_mismatch(self) -> None:
        """Increment guard config schema mismatch counter."""
        self._guard_config_schema_mismatch_total.inc()

    def set_guard_config_loaded(self, schema_version: str, config_version: str) -> None:
        """Set active guard config gauge."""
        self._guard_config_loaded.labels(
            schema_version=schema_version, config_version=config_version
        ).set(1)

    def inc_slo_violation(self, slo_name: str) -> None:
        """Increment SLO violation counter. slo_name from fixed enum."""
        self._slo_violation_total.labels(slo_name=slo_name).inc()

    def inc_sentinel_impossible_state(self) -> None:
        """Increment impossible state sentinel counter."""
        self._sentinel_impossible_state_total.inc()

    def set_killswitch_state(self, switch_name: str, active: bool) -> None:
        """Set kill-switch gauge (1=active, 0=passive)."""
        self._killswitch_state.labels(switch_name=switch_name).set(1 if active else 0)

    def inc_killswitch_error(self, endpoint_class: str, error_type: str) -> None:
        """Increment kill-switch error counter (HD-1)."""
        self._killswitch_error_total.labels(
            endpoint_class=endpoint_class, error_type=error_type
        ).inc()

    def inc_killswitch_fallback_open(self) -> None:
        """Increment kill-switch fail-open fallback counter (HD-1)."""
        self._killswitch_fallback_open_total.inc()

    def inc_rate_limit(self, endpoint: str, decision: str) -> None:
        """Increment rate limit decision counter. decision: 'allowed' | 'rejected'."""
        self._rate_limit_total.labels(endpoint=endpoint, decision=decision).inc()

    def set_circuit_breaker_state(self, dependency: str, state: int) -> None:
        """Set circuit breaker state gauge. state: 0=closed, 1=half-open, 2=open."""
        self._circuit_breaker_state.labels(dependency=dependency).set(state)

    # ── Dependency Wrapper metrics (Feature: dependency-wrappers, Task 2) ─

    _VALID_DEP_OUTCOMES = frozenset({"success", "failure", "timeout", "circuit_open", "client_error"})

    def inc_dependency_call(self, dependency: str, outcome: str) -> None:
        """Increment dependency call counter. outcome: success|failure|timeout|circuit_open|client_error."""
        if outcome not in self._VALID_DEP_OUTCOMES:
            logger.warning(f"[METRICS] Invalid dependency outcome: {outcome}")
            return
        self._dependency_call_total.labels(dependency=dependency, outcome=outcome).inc()

    def observe_dependency_call_duration(self, dependency: str, duration: float) -> None:
        """Record dependency call duration."""
        self._dependency_call_duration.labels(dependency=dependency).observe(duration)

    def inc_dependency_retry(self, dependency: str) -> None:
        """Increment dependency retry counter."""
        self._dependency_retry_total.labels(dependency=dependency).inc()

    def inc_guard_failopen(self) -> None:
        """Increment guard fail-open counter (DW-3: middleware + wrapper)."""
        self._guard_failopen_total.inc()

    def inc_dependency_map_miss(self) -> None:
        """Increment dependency map miss counter (endpoint not in mapping)."""
        self._dependency_map_miss_total.inc()

    # ── Snapshot (test/debug only) ────────────────────────────────────────

    def snapshot(self) -> Dict:
        """
        Return a snapshot of all metrics in the legacy dict format.

        Reads values from prometheus_client collectors.
        Intended for test/debug purposes only — SHALL NOT be used
        in production request paths.
        """
        return {
            "import_apply_duration_seconds": self._snapshot_histogram(
                self._import_apply_duration
            ),
            "import_rows_total": {
                "accepted": self._get_counter_value(
                    self._import_rows_total, {"outcome": "accepted"}
                ),
                "rejected": self._get_counter_value(
                    self._import_rows_total, {"outcome": "rejected"}
                ),
            },
            "upsert_total": {
                "provisional": self._get_counter_value(
                    self._upsert_total, {"status": "provisional"}
                ),
                "final": self._get_counter_value(
                    self._upsert_total, {"status": "final"}
                ),
            },
            "lookup_total": {
                "hit=true,status=final": self._get_counter_value(
                    self._lookup_total, {"hit": "true", "status": "final"}
                ),
                "hit=true,status=provisional": self._get_counter_value(
                    self._lookup_total, {"hit": "true", "status": "provisional"}
                ),
                "hit=false": self._get_counter_value(
                    self._lookup_total, {"hit": "false", "status": ""}
                ),
            },
        }

    @staticmethod
    def _get_counter_value(counter: Counter, labels: Dict[str, str]) -> int:
        """Read current value of a labeled counter. Returns 0 if label combo not yet initialized."""
        try:
            return int(counter.labels(**labels)._value.get())
        except Exception:
            return 0

    @staticmethod
    def _snapshot_histogram(histogram: Histogram) -> Dict:
        """Read count and total from a histogram (no labels)."""
        try:
            total = histogram._sum.get()
            # Histogram has no _count attr; read from collect() samples
            count = 0.0
            for sample in histogram.collect()[0].samples:
                if sample.name.endswith("_count"):
                    count = sample.value
                    break
            return {
                "count": int(count),
                "total_seconds": round(total, 6),
            }
        except Exception:
            return {"count": 0, "total_seconds": 0.0}

    # ── Reset (test only) ─────────────────────────────────────────────────

    def reset(self) -> None:
        """
        Reset all metrics by creating a fresh CollectorRegistry.

        SHALL only be called in test environments.
        """
        self._registry = CollectorRegistry()
        self._init_metrics()

    # ── Prometheus exposition ─────────────────────────────────────────────

    def generate_metrics(self) -> bytes:
        """Generate Prometheus text exposition format output."""
        return generate_latest(self._registry)

    @property
    def registry(self) -> CollectorRegistry:
        """Access the underlying CollectorRegistry."""
        return self._registry


# ═══════════════════════════════════════════════════════════════════════════════
# SINGLETON INSTANCE
# ═══════════════════════════════════════════════════════════════════════════════

_metrics = PTFMetrics()


def get_ptf_metrics() -> PTFMetrics:
    """Get singleton PTFMetrics instance."""
    return _metrics
