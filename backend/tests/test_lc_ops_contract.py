"""
PR-4 Part C: Observability Ops Contract.

Minimum viable ops contract tests for LC context:
- Label/namespace invariants on alert YAML
- Runbook anchor reachability for LC-relevant alerts
- Metric cardinality guard (bounded label sets)
- Alert rule drift detection (semantic, not snapshot)
"""
import re

import pytest
import yaml
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from backend.app.testing.alert_validator import AlertValidator
from backend.app.testing.lc_config import FaultType, FM_EXPECTS_CB_OPEN
from backend.app.guards.circuit_breaker import Dependency


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ALERTS_PATH = "monitoring/prometheus/ptf-admin-alerts.yml"


def _load_alert_data():
    with open(ALERTS_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _all_rules(data):
    rules = []
    for group in data["spec"]["groups"]:
        rules.extend(group["rules"])
    return rules


ALERT_DATA = _load_alert_data()
ALL_RULES = _all_rules(ALERT_DATA)

# LC-relevant alert names (the ones AlertValidator checks)
LC_ALERT_NAMES = [
    "PTFAdminCircuitBreakerOpen",
    "PTFAdminRateLimitSpike",
    "PTFAdminGuardInternalError",
]


# ---------------------------------------------------------------------------
# OPS-1: Label/namespace invariants
# ---------------------------------------------------------------------------

class TestLabelNamespaceInvariants:
    """Every alert rule must have consistent label contract."""

    REQUIRED_LABELS = {"severity", "team", "service"}
    VALID_SEVERITIES = {"critical", "warning"}

    @given(rule_idx=st.sampled_from(list(range(len(ALL_RULES)))))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_pbt_all_rules_have_required_labels(self, rule_idx: int):
        rule = ALL_RULES[rule_idx]
        labels = rule.get("labels", {})
        missing = self.REQUIRED_LABELS - labels.keys()
        assert not missing, f"Alert '{rule['alert']}' missing labels: {missing}"

    @given(rule_idx=st.sampled_from(list(range(len(ALL_RULES)))))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_pbt_service_label_is_ptf_admin(self, rule_idx: int):
        rule = ALL_RULES[rule_idx]
        assert rule["labels"]["service"] == "ptf-admin"

    @given(rule_idx=st.sampled_from(list(range(len(ALL_RULES)))))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_pbt_severity_is_valid(self, rule_idx: int):
        rule = ALL_RULES[rule_idx]
        assert rule["labels"]["severity"] in self.VALID_SEVERITIES

    def test_metadata_labels_contain_prometheus(self):
        """CRD metadata must have prometheus: kube-prometheus label."""
        labels = ALERT_DATA["metadata"]["labels"]
        assert labels.get("prometheus") == "kube-prometheus"

    def test_metadata_has_app_label(self):
        labels = ALERT_DATA["metadata"]["labels"]
        assert labels.get("app") == "ptf-admin"


# ---------------------------------------------------------------------------
# OPS-2: Runbook anchor validation for LC-relevant alerts
# ---------------------------------------------------------------------------

class TestRunbookAnchorValidation:
    """LC-relevant alerts must have valid runbook_url with matching anchor."""

    def _get_rule_by_name(self, name: str):
        for r in ALL_RULES:
            if r.get("alert") == name:
                return r
        pytest.fail(f"Alert {name} not found in YAML")

    @pytest.mark.parametrize("alert_name", LC_ALERT_NAMES)
    def test_lc_alert_has_runbook_url(self, alert_name: str):
        rule = self._get_rule_by_name(alert_name)
        url = rule.get("annotations", {}).get("runbook_url", "")
        assert url, f"{alert_name} missing runbook_url"
        assert "#" in url, f"{alert_name} runbook_url has no anchor fragment"

    @pytest.mark.parametrize("alert_name", LC_ALERT_NAMES)
    def test_lc_alert_runbook_anchor_matches_name(self, alert_name: str):
        rule = self._get_rule_by_name(alert_name)
        url = rule["annotations"]["runbook_url"]
        anchor = url.split("#")[-1]
        assert alert_name.lower() in anchor.lower(), (
            f"{alert_name}: anchor '{anchor}' doesn't match alert name"
        )

    @given(rule_idx=st.sampled_from(list(range(len(ALL_RULES)))))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_pbt_all_alerts_have_runbook_url(self, rule_idx: int):
        """Every alert (not just LC ones) must have a runbook_url."""
        rule = ALL_RULES[rule_idx]
        url = rule.get("annotations", {}).get("runbook_url", "")
        assert url, f"Alert '{rule['alert']}' missing runbook_url"


# ---------------------------------------------------------------------------
# OPS-3: Metric cardinality guard
# ---------------------------------------------------------------------------

class TestMetricCardinalityGuard:
    """Dependency label set must be bounded to known Dependency enum values."""

    def test_dependency_enum_is_bounded(self):
        """Dependency enum has a finite, known set of values."""
        deps = list(Dependency)
        assert len(deps) > 0
        assert len(deps) <= 20, f"Dependency enum has {len(deps)} values — cardinality risk"

    def test_fm_fault_types_bounded(self):
        """FaultType enum is bounded."""
        assert len(list(FaultType)) == 5

    def test_fm_expects_cb_map_covers_all_fault_types(self):
        """FM_EXPECTS_CB_OPEN covers every FaultType."""
        for ft in FaultType:
            assert ft in FM_EXPECTS_CB_OPEN, f"{ft} missing from FM_EXPECTS_CB_OPEN"

    @given(dep_idx=st.sampled_from(list(range(len(list(Dependency))))))
    @settings(max_examples=20, suppress_health_check=[HealthCheck.too_slow])
    def test_pbt_dependency_values_are_strings(self, dep_idx: int):
        """Each Dependency value is a non-empty string."""
        dep = list(Dependency)[dep_idx]
        assert isinstance(dep.value, str)
        assert len(dep.value) > 0


# ---------------------------------------------------------------------------
# OPS-4: Alert rule drift detection (semantic)
# ---------------------------------------------------------------------------

class TestAlertRuleDrift:
    """Detect semantic drift in LC-relevant alert rules."""

    def test_cb_open_alert_threshold_is_state_2(self):
        """PTFAdminCircuitBreakerOpen must check for state == 2."""
        for r in ALL_RULES:
            if r.get("alert") == "PTFAdminCircuitBreakerOpen":
                assert "== 2" in r["expr"], "CB alert expr must check state == 2"
                return
        pytest.fail("PTFAdminCircuitBreakerOpen not found")

    def test_rate_limit_alert_has_deny_filter(self):
        """PTFAdminRateLimitSpike must filter on decision=deny."""
        for r in ALL_RULES:
            if r.get("alert") == "PTFAdminRateLimitSpike":
                assert 'decision="deny"' in r["expr"]
                return
        pytest.fail("PTFAdminRateLimitSpike not found")

    def test_guard_error_alert_checks_both_counters(self):
        """PTFAdminGuardInternalError must check both error and fallback counters."""
        for r in ALL_RULES:
            if r.get("alert") == "PTFAdminGuardInternalError":
                expr = r["expr"]
                assert "killswitch_error_total" in expr
                assert "killswitch_fallback_open_total" in expr
                return
        pytest.fail("PTFAdminGuardInternalError not found")

    def test_alert_validator_agrees_with_yaml(self):
        """AlertValidator's loaded alert names match YAML alert names."""
        validator = AlertValidator()
        yaml_names = {r["alert"] for r in ALL_RULES}
        validator_names = set(validator.alert_names)
        assert validator_names == yaml_names, (
            f"Drift detected: validator={validator_names - yaml_names}, "
            f"yaml={yaml_names - validator_names}"
        )
