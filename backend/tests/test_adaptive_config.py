"""
Tests for adaptive control config, allowlist, and canonical SLO signals.

Feature: slo-adaptive-control, Tasks 1.4–1.8
MUST Properties: P4 (Allowlist Scoping), P21 (Config Validation)
Optional Properties: P10 (Config Drift), P22 (Config Change Audit)
"""

from __future__ import annotations

import json

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from backend.app.adaptive_control.config import (
    CANONICAL_GUARD_SLO_QUERY,
    CANONICAL_PDF_SLO_QUERY,
    AdaptiveControlConfig,
    AllowlistEntry,
    AllowlistManager,
    check_config_drift,
    load_adaptive_control_config,
)


# ── Hypothesis Strategies ─────────────────────────────────────────────────────

allowlist_entry_st = st.builds(
    AllowlistEntry,
    tenant_id=st.sampled_from(["tenant_a", "tenant_b", "tenant_c", "*"]),
    endpoint_class=st.sampled_from(["high", "medium", "low", "*"]),
    subsystem_id=st.sampled_from(["guard", "pdf", "*"]),
)

target_st = st.fixed_dictionaries({
    "tenant_id": st.sampled_from(["tenant_a", "tenant_b", "tenant_x", "tenant_z"]),
    "endpoint_class": st.sampled_from(["high", "medium", "low", "critical"]),
    "subsystem_id": st.sampled_from(["guard", "pdf", "cache"]),
})


# ══════════════════════════════════════════════════════════════════════════════
# MUST Property P21: Configuration Validation
# Random invalid config values → validate() returns errors, config preserved.
# Validates: Req 9.2
# ══════════════════════════════════════════════════════════════════════════════

class TestConfigValidationPropertyP21:
    """MUST — Property 21: Configuration Validation."""

    @given(
        enter=st.floats(min_value=0.01, max_value=10.0),
        exit_val=st.floats(min_value=0.01, max_value=10.0),
    )
    @settings(max_examples=100, derandomize=True, suppress_health_check=[HealthCheck.too_slow])
    def test_exit_ge_enter_rejected(self, enter: float, exit_val: float):
        """If exit >= enter, validate() must return error."""
        config = AdaptiveControlConfig(
            p95_latency_enter_threshold=enter,
            p95_latency_exit_threshold=exit_val,
        )
        errors = config.validate()
        if exit_val >= enter:
            assert any("p95_latency_exit" in e for e in errors)
        # If exit < enter, this specific error should not appear
        if exit_val < enter:
            assert not any("p95_latency_exit" in e and "must be <" in e for e in errors)

    @given(
        guard_target=st.floats(min_value=-1.0, max_value=2.0),
        pdf_target=st.floats(min_value=-1.0, max_value=2.0),
    )
    @settings(max_examples=100, derandomize=True, suppress_health_check=[HealthCheck.too_slow])
    def test_slo_target_range(self, guard_target: float, pdf_target: float):
        """SLO targets outside (0, 1] must be rejected."""
        config = AdaptiveControlConfig(
            guard_slo_target=guard_target,
            pdf_slo_target=pdf_target,
        )
        errors = config.validate()
        if not (0.0 < guard_target <= 1.0):
            assert any("guard_slo_target" in e for e in errors)
        if not (0.0 < pdf_target <= 1.0):
            assert any("pdf_slo_target" in e for e in errors)

    @given(
        loop_interval=st.floats(min_value=-100.0, max_value=100.0),
        dwell=st.floats(min_value=-100.0, max_value=100.0),
        cooldown=st.floats(min_value=-100.0, max_value=100.0),
    )
    @settings(max_examples=100, derandomize=True, suppress_health_check=[HealthCheck.too_slow])
    def test_positive_durations(self, loop_interval: float, dwell: float, cooldown: float):
        """Non-positive durations must be rejected."""
        config = AdaptiveControlConfig(
            control_loop_interval_seconds=loop_interval,
            dwell_time_seconds=dwell,
            cooldown_period_seconds=cooldown,
        )
        errors = config.validate()
        if loop_interval <= 0:
            assert any("control_loop_interval" in e for e in errors)
        if dwell <= 0:
            assert any("dwell_time" in e for e in errors)
        if cooldown <= 0:
            assert any("cooldown_period" in e for e in errors)

    @given(burn_rate=st.floats(min_value=-10.0, max_value=10.0))
    @settings(max_examples=100, derandomize=True, suppress_health_check=[HealthCheck.too_slow])
    def test_burn_rate_positive(self, burn_rate: float):
        """Burn rate must be > 0."""
        config = AdaptiveControlConfig(burn_rate_threshold=burn_rate)
        errors = config.validate()
        if burn_rate <= 0:
            assert any("burn_rate" in e for e in errors)
        else:
            assert not any("burn_rate" in e for e in errors)

    def test_valid_default_config(self):
        """Default config must pass validation."""
        config = AdaptiveControlConfig()
        errors = config.validate()
        assert errors == [], f"Default config should be valid, got: {errors}"


# ══════════════════════════════════════════════════════════════════════════════
# MUST Property P4: Allowlist Scoping Invariant
# Allowlist dışı hedef için sinyal üretilmez; boş allowlist → sıfır sinyal.
# Validates: Req CC.5, 7.5, 9.5, 9.6
# ══════════════════════════════════════════════════════════════════════════════

class TestAllowlistScopingPropertyP4:
    """MUST — Property 4: Allowlist Scoping Invariant."""

    @given(
        entries=st.lists(allowlist_entry_st, min_size=0, max_size=5),
        target=target_st,
    )
    @settings(max_examples=100, derandomize=True, suppress_health_check=[HealthCheck.too_slow])
    def test_empty_allowlist_always_false(self, entries: list, target: dict):
        """Empty allowlist → is_in_scope() always False."""
        mgr = AllowlistManager([])
        assert mgr.is_in_scope(**target) is False

    @given(
        entries=st.lists(allowlist_entry_st, min_size=1, max_size=5),
        target=target_st,
    )
    @settings(max_examples=100, derandomize=True, suppress_health_check=[HealthCheck.too_slow])
    def test_out_of_scope_never_matches(self, entries: list, target: dict):
        """Target not matching any entry → is_in_scope() False."""
        mgr = AllowlistManager(entries)
        result = mgr.is_in_scope(**target)
        # Verify: if result is True, at least one entry must match
        if result:
            matched = False
            for entry in entries:
                t_match = entry.tenant_id == "*" or entry.tenant_id == target["tenant_id"]
                e_match = entry.endpoint_class == "*" or entry.endpoint_class == target["endpoint_class"]
                s_match = entry.subsystem_id == "*" or entry.subsystem_id == target["subsystem_id"]
                if t_match and e_match and s_match:
                    matched = True
                    break
            assert matched, "is_in_scope returned True but no entry matches"

    def test_empty_allowlist_is_empty(self):
        """Empty allowlist property."""
        mgr = AllowlistManager([])
        assert mgr.is_empty is True
        assert mgr.is_in_scope(tenant_id="any") is False

    def test_wildcard_entry_matches_all(self):
        """Wildcard entry matches any target."""
        mgr = AllowlistManager([AllowlistEntry()])  # all wildcards
        assert mgr.is_in_scope(tenant_id="x", endpoint_class="y", subsystem_id="z") is True

    def test_specific_entry_matches_exact(self):
        """Specific entry matches only exact target."""
        mgr = AllowlistManager([
            AllowlistEntry(tenant_id="t1", endpoint_class="high", subsystem_id="guard"),
        ])
        assert mgr.is_in_scope(tenant_id="t1", endpoint_class="high", subsystem_id="guard") is True
        assert mgr.is_in_scope(tenant_id="t2", endpoint_class="high", subsystem_id="guard") is False


# ══════════════════════════════════════════════════════════════════════════════
# Optional Property P10: Config Drift Detection
# Validates: Req 2.5
# ══════════════════════════════════════════════════════════════════════════════

class TestConfigDriftPropertyP10:
    """Optional — Property 10: Config Drift Detection."""

    @given(
        guard_query=st.text(min_size=1, max_size=50),
        pdf_query=st.text(min_size=1, max_size=50),
    )
    @settings(max_examples=100, derandomize=True, suppress_health_check=[HealthCheck.too_slow])
    def test_non_canonical_query_detected(self, guard_query: str, pdf_query: str):
        """Non-canonical queries → config_drift_detected error."""
        config = AdaptiveControlConfig(
            guard_slo_query=guard_query,
            pdf_slo_query=pdf_query,
        )
        drift = check_config_drift(config)
        guard_matches = guard_query == CANONICAL_GUARD_SLO_QUERY
        pdf_matches = pdf_query == CANONICAL_PDF_SLO_QUERY
        if guard_matches and pdf_matches:
            assert drift is None
        else:
            assert drift is not None
            assert "config_drift_detected" in drift

    def test_canonical_queries_no_drift(self):
        """Default canonical queries → no drift."""
        config = AdaptiveControlConfig()
        assert check_config_drift(config) is None


# ══════════════════════════════════════════════════════════════════════════════
# Optional Property P22: Configuration Change Audit
# Validates: Req 9.3, 9.7
# ══════════════════════════════════════════════════════════════════════════════

class TestConfigChangeAuditPropertyP22:
    """Optional — Property 22: Configuration Change Audit."""

    @given(
        old_entries=st.lists(allowlist_entry_st, min_size=0, max_size=3),
        new_entries=st.lists(allowlist_entry_st, min_size=0, max_size=3),
    )
    @settings(max_examples=100, derandomize=True, suppress_health_check=[HealthCheck.too_slow])
    def test_allowlist_update_produces_audit(self, old_entries: list, new_entries: list):
        """Every allowlist update produces audit record with required fields."""
        mgr = AllowlistManager(old_entries)
        audit = mgr.update(new_entries, actor="test_actor")
        assert "action" in audit
        assert audit["action"] == "allowlist_update"
        assert "old_entries" in audit
        assert "new_entries" in audit
        assert "actor" in audit
        assert audit["actor"] == "test_actor"
        assert "timestamp" in audit
        assert len(audit["new_entries"]) == len(new_entries)


# ══════════════════════════════════════════════════════════════════════════════
# Unit Tests (Task 1.8)
# ══════════════════════════════════════════════════════════════════════════════

class TestCanonicalSloSignals:
    """Unit tests for canonical SLO signal configuration."""

    def test_canonical_guard_slo_signal_config(self):
        """Req 2.2: Guard canonical signal is p95 API latency over 5m."""
        config = AdaptiveControlConfig()
        assert "http_request_duration_seconds_bucket" in config.guard_slo_query
        assert "0.95" in config.guard_slo_query
        assert "5m" in config.guard_slo_query

    def test_canonical_pdf_slo_signal_config(self):
        """Req 2.3: PDF canonical signal is p95 PDF render duration over 5m."""
        config = AdaptiveControlConfig()
        assert "pdf_render_duration_seconds_bucket" in config.pdf_slo_query
        assert "0.95" in config.pdf_slo_query
        assert "5m" in config.pdf_slo_query

    def test_error_budget_config_format(self):
        """Req 3.3: Error budget config has metric, window, threshold."""
        config = AdaptiveControlConfig()
        assert config.error_budget_window_seconds == 30 * 86400  # 30 days
        assert 0.0 < config.guard_slo_target <= 1.0
        assert 0.0 < config.pdf_slo_target <= 1.0
        assert config.burn_rate_threshold > 0

    def test_default_config_from_env(self):
        """Req 9.4: Config loads from env vars with ADAPTIVE_CONTROL_ prefix."""
        env = {
            "ADAPTIVE_CONTROL_LOOP_INTERVAL": "15.0",
            "ADAPTIVE_CONTROL_P95_LATENCY_ENTER": "1.0",
            "ADAPTIVE_CONTROL_QUEUE_DEPTH_ENTER": "100",
            "ADAPTIVE_CONTROL_GUARD_SLO_TARGET": "0.995",
        }
        config = load_adaptive_control_config(env=env)
        assert config.control_loop_interval_seconds == 15.0
        assert config.p95_latency_enter_threshold == 1.0
        assert config.queue_depth_enter_threshold == 100
        assert config.guard_slo_target == 0.995

    def test_separate_enter_exit_thresholds(self):
        """Req 5.1: Separate enter and exit thresholds for hysteresis."""
        config = AdaptiveControlConfig()
        assert config.p95_latency_exit_threshold < config.p95_latency_enter_threshold
        assert config.queue_depth_exit_threshold < config.queue_depth_enter_threshold

    def test_empty_allowlist_no_action(self):
        """Edge case: empty allowlist → no targets in scope."""
        config = AdaptiveControlConfig(targets=[])
        mgr = AllowlistManager(config.targets)
        assert mgr.is_empty is True
        assert mgr.is_in_scope(tenant_id="any", subsystem_id="guard") is False

    def test_invalid_env_falls_back_to_defaults(self):
        """Invalid env values → fallback to defaults."""
        env = {"ADAPTIVE_CONTROL_LOOP_INTERVAL": "not_a_number"}
        config = load_adaptive_control_config(env=env)
        assert config.control_loop_interval_seconds == 30.0  # default

    def test_targets_json_loading(self):
        """Allowlist targets loaded from JSON env var."""
        targets = [
            {"tenant_id": "t1", "endpoint_class": "high", "subsystem_id": "guard"},
            {"tenant_id": "t2"},
        ]
        env = {"ADAPTIVE_CONTROL_TARGETS_JSON": json.dumps(targets)}
        config = load_adaptive_control_config(env=env)
        assert len(config.targets) == 2
        assert config.targets[0].tenant_id == "t1"
        assert config.targets[1].tenant_id == "t2"
        assert config.targets[1].endpoint_class == "*"  # default wildcard

    def test_invalid_targets_json_empty_allowlist(self):
        """Invalid JSON → empty allowlist."""
        env = {"ADAPTIVE_CONTROL_TARGETS_JSON": "not json"}
        config = load_adaptive_control_config(env=env)
        assert config.targets == []

    def test_validation_rejects_invalid_and_falls_back(self):
        """Invalid config from env → falls back to safe defaults."""
        env = {
            "ADAPTIVE_CONTROL_P95_LATENCY_ENTER": "0.1",
            "ADAPTIVE_CONTROL_P95_LATENCY_EXIT": "0.5",  # exit > enter = invalid
        }
        config = load_adaptive_control_config(env=env)
        # Should have fallen back to defaults
        assert config.p95_latency_enter_threshold == 0.5
        assert config.p95_latency_exit_threshold == 0.3
